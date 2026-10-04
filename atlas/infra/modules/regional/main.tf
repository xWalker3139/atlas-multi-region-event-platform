terraform {
  required_providers {
    aws = {
      source = "hashicorp/aws"
    }
  }
}
variable "name" {
  type = string
}
variable "role" {
  type = string
}
variable "domain" {
  type = string
}
variable "zone_id" {
  type = string
}
variable "lambda_zip" {
  type = string
}
variable "db_host" {
  type = string
}
variable "db_password" {
  type = string
  sensitive = true
}
variable "api_token" {
  type = string
  sensitive = true
}
variable "regions" {
  type = string
}
variable "subnet_ids" {
  type = list(string)
}
variable "lambda_sg" {
  type = string
}
data "aws_region" "current" {
}
data "aws_caller_identity" "current" {
}
locals {
  prefix = "${var.name}-${var.role}"
  functions = {
    api       = {
      handler = "atlas.handlers.api", timeout = 25, concurrency = 100
    }
    publisher = {
      handler = "atlas.handlers.publisher", timeout = 60, concurrency = 16
    }
    consumer  = {
      handler = "atlas.handlers.consumer", timeout = 25, concurrency = 100
    }
    migrate   = {
      handler = "atlas.handlers.migrate", timeout = 60, concurrency = 1
    }
  }
}
resource "aws_secretsmanager_secret" "db" {
  name = "${local.prefix}-db"
}
resource "aws_secretsmanager_secret_version" "db" {
  secret_id     = aws_secretsmanager_secret.db.id
  secret_string = jsonencode({
    username = "atlas_admin", password = var.db_password
  })
}
resource "aws_secretsmanager_secret" "api" {
  name = "${local.prefix}-api"
}
resource "aws_secretsmanager_secret_version" "api" {
  secret_id     = aws_secretsmanager_secret.api.id
  secret_string = jsonencode({
    token = var.api_token
  })
}
resource "aws_sqs_queue" "dlq" {
  name                      = "${local.prefix}-dlq"
  message_retention_seconds = 1209600
  sqs_managed_sse_enabled   = true
}
resource "aws_sqs_queue" "events" {
  name                       = "${local.prefix}-events"
  visibility_timeout_seconds = 180
  message_retention_seconds  = 1209600
  receive_wait_time_seconds  = 20
  sqs_managed_sse_enabled    = true
  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.dlq.arn, maxReceiveCount = 5
  })
}
resource "aws_sqs_queue_redrive_allow_policy" "dlq" {
  queue_url = aws_sqs_queue.dlq.id
  redrive_allow_policy = jsonencode({
    redrivePermission = "byQueue", sourceQueueArns = [aws_sqs_queue.events.arn]
  })
}
resource "aws_s3_bucket" "archive" {
  bucket = "${local.prefix}-${data.aws_caller_identity.current.account_id}-events"
}
resource "aws_s3_bucket_versioning" "archive" {
  bucket = aws_s3_bucket.archive.id
  versioning_configuration {
    status = "Enabled"
  }
}
resource "aws_s3_bucket_server_side_encryption_configuration" "archive" {
  bucket = aws_s3_bucket.archive.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}
resource "aws_s3_bucket_public_access_block" "archive" {
  bucket                  = aws_s3_bucket.archive.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}
resource "aws_s3_bucket_policy" "tls" {
  bucket = aws_s3_bucket.archive.id
  policy = jsonencode({
    Version = "2012-10-17", Statement = [{
      Effect = "Deny", Principal = "*", Action = "s3:*",
      Resource = [aws_s3_bucket.archive.arn, "${aws_s3_bucket.archive.arn}/*"],
      Condition = {
        Bool = {
          "aws:SecureTransport" = "false"
        }
      }
    }]
  })
}
resource "aws_cloudwatch_log_group" "function" {
  for_each          = local.functions
  name              = "/aws/lambda/${local.prefix}-${each.key}"
  retention_in_days = 30
}
resource "aws_iam_role" "function" {
  for_each = local.functions
  name     = "${local.prefix}-${each.key}"
  assume_role_policy = jsonencode({
    Version = "2012-10-17", Statement = [{
      Effect = "Allow", Principal = {
        Service = "lambda.amazonaws.com"
      }, Action = "sts:AssumeRole"
    }]
  })
}
resource "aws_iam_role_policy_attachment" "vpc" {
  for_each   = local.functions
  role       = aws_iam_role.function[each.key].name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole"
}
resource "aws_iam_role_policy" "function" {
  for_each = local.functions
  role     = aws_iam_role.function[each.key].id
  policy = jsonencode({
    Version = "2012-10-17", Statement = concat([
    {
      Effect = "Allow", Action = ["secretsmanager:GetSecretValue"], Resource = [aws_secretsmanager_secret.db.arn]
    }
    ], each.key == "api" ? [
    {
      Effect = "Allow", Action = ["secretsmanager:GetSecretValue"], Resource = [aws_secretsmanager_secret.api.arn]
    }
    ] : [], each.key == "publisher" ? [
    {
      Effect = "Allow", Action = ["sqs:SendMessage"], Resource = [aws_sqs_queue.events.arn]
    },
    {
      Effect = "Allow", Action = ["s3:PutObject"], Resource = ["${aws_s3_bucket.archive.arn}/events/*"]
    }
    ] : [], each.key == "consumer" ? [
    {
      Effect = "Allow", Action = ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes"], Resource = [aws_sqs_queue.events.arn]
    }
    ] : [])
  })
}
resource "aws_lambda_function" "function" {
  for_each                       = local.functions
  function_name                  = "${local.prefix}-${each.key}"
  filename                       = var.lambda_zip
  source_code_hash               = filebase64sha256(var.lambda_zip)
  role                           = aws_iam_role.function[each.key].arn
  runtime                        = "python3.12"
  handler                        = each.value.handler
  timeout                        = each.value.timeout
  memory_size                    = 1024
  reserved_concurrent_executions = each.value.concurrency
  vpc_config {
    subnet_ids         = var.subnet_ids
    security_group_ids = [var.lambda_sg]
  }
  environment {
    variables = {
      DB_HOST        = var.db_host
      DB_SECRET_ARN  = aws_secretsmanager_secret.db.arn
      API_SECRET_ARN = aws_secretsmanager_secret.api.arn
      QUEUE_URL      = aws_sqs_queue.events.url
      ARCHIVE_BUCKET = aws_s3_bucket.archive.id
      REGIONS        = var.regions
    }
  }
  depends_on = [aws_iam_role_policy.function, aws_iam_role_policy_attachment.vpc, aws_cloudwatch_log_group.function,
  aws_secretsmanager_secret_version.db, aws_secretsmanager_secret_version.api]
}
resource "aws_lambda_event_source_mapping" "consumer" {
  event_source_arn        = aws_sqs_queue.events.arn
  function_name          = aws_lambda_function.function["consumer"].arn
  batch_size             = 10
  function_response_types = ["ReportBatchItemFailures"]
  scaling_config {
    maximum_concurrency = 80
  }
}
resource "aws_cloudwatch_event_rule" "publisher" {
  count               = 16
  name                = "${local.prefix}-publish-${count.index}"
  schedule_expression = "rate(1 minute)"
}
resource "aws_cloudwatch_event_target" "publisher" {
  count = 16
  rule  = aws_cloudwatch_event_rule.publisher[count.index].name
  arn   = aws_lambda_function.function["publisher"].arn
  input = jsonencode({
    shard = count.index
  })
}
resource "aws_lambda_permission" "publisher" {
  count         = 16
  statement_id  = "schedule-${count.index}"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.function["publisher"].function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.publisher[count.index].arn
}
resource "aws_apigatewayv2_api" "this" {
  name          = local.prefix
  protocol_type = "HTTP"
}
resource "aws_apigatewayv2_integration" "lambda" {
  api_id                 = aws_apigatewayv2_api.this.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.function["api"].invoke_arn
  payload_format_version = "2.0"
  timeout_milliseconds   = 29000
}
resource "aws_apigatewayv2_route" "default" {
  api_id    = aws_apigatewayv2_api.this.id
  route_key = "$default"
  target    = "integrations/${aws_apigatewayv2_integration.lambda.id}"
}
resource "aws_apigatewayv2_stage" "default" {
  api_id      = aws_apigatewayv2_api.this.id
  name        = "$default"
  auto_deploy = true
  default_route_settings {
    throttling_burst_limit = 1000
    throttling_rate_limit = 500
  }
}
resource "aws_lambda_permission" "api" {
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.function["api"].function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.this.execution_arn}/*/*"
}
resource "aws_acm_certificate" "this" {
  domain_name       = var.domain
  validation_method = "DNS"
  lifecycle {
    create_before_destroy = true
  }
}
resource "aws_route53_record" "validation" {
  for_each = var.role == "primary" ? {
    for o in aws_acm_certificate.this.domain_validation_options : o.domain_name => o
  } : {}
  zone_id  = var.zone_id
  name     = each.value.resource_record_name
  type     = each.value.resource_record_type
  records  = [each.value.resource_record_value]
  ttl      = 60
  allow_overwrite = true
}
resource "aws_acm_certificate_validation" "this" {
  certificate_arn         = aws_acm_certificate.this.arn
  validation_record_fqdns = [for o in aws_acm_certificate.this.domain_validation_options : o.resource_record_name]
  depends_on = [aws_route53_record.validation]
}
resource "aws_apigatewayv2_domain_name" "this" {
  domain_name = var.domain
  domain_name_configuration {
    certificate_arn = aws_acm_certificate_validation.this.certificate_arn
    endpoint_type   = "REGIONAL"
    security_policy = "TLS_1_2"
  }
}
resource "aws_apigatewayv2_api_mapping" "this" {
  api_id      = aws_apigatewayv2_api.this.id
  domain_name = aws_apigatewayv2_domain_name.this.id
  stage       = aws_apigatewayv2_stage.default.name
}
resource "aws_route53_health_check" "this" {
  fqdn              = replace(aws_apigatewayv2_api.this.api_endpoint, "https://", "")
  port              = 443
  type              = "HTTPS"
  resource_path     = "/health"
  request_interval  = 30
  failure_threshold = 3
  enable_sni        = true
}
resource "aws_route53_record" "this" {
  zone_id         = var.zone_id
  name            = var.domain
  type            = "A"
  set_identifier  = var.role
  health_check_id = aws_route53_health_check.this.id
  failover_routing_policy {
    type = upper(var.role)
  }
  alias {
    name                   = aws_apigatewayv2_domain_name.this.domain_name_configuration[0].target_domain_name
    zone_id                = aws_apigatewayv2_domain_name.this.domain_name_configuration[0].hosted_zone_id
    evaluate_target_health = false
  }
}
resource "aws_cloudwatch_metric_alarm" "dlq" {
  alarm_name          = "${local.prefix}-dlq-nonempty"
  namespace           = "AWS/SQS"
  metric_name         = "ApproximateNumberOfMessagesVisible"
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  dimensions          = {
    QueueName = aws_sqs_queue.dlq.name
  }
}
resource "aws_cloudwatch_metric_alarm" "age" {
  alarm_name          = "${local.prefix}-event-age"
  namespace           = "AWS/SQS"
  metric_name         = "ApproximateAgeOfOldestMessage"
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 2
  threshold           = 120
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  dimensions          = {
    QueueName = aws_sqs_queue.events.name
  }
}
output "api_url" {
  value = aws_apigatewayv2_api.this.api_endpoint
}
output "api_secret_arn" {
  value = aws_secretsmanager_secret.api.arn
}
output "migration_function" {
  value = aws_lambda_function.function["migrate"].function_name
}
output "queue_url" {
  value = aws_sqs_queue.events.url
}
output "archive_bucket" {
  value = aws_s3_bucket.archive.id
}
