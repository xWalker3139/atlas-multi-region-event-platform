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
variable "enabled" {
  type = bool
}
variable "lambda_zip" {
  type = string
}
variable "global_cluster" {
  type = string
}
variable "primary_cluster_arn" {
  type = string
}
variable "secondary_cluster_arn" {
  type = string
}
variable "primary_url" {
  type = string
}
variable "secondary_url" {
  type = string
}
data "aws_caller_identity" "current" {
}
resource "aws_dynamodb_table" "control" {
  name         = "${var.name}-failover-control"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "id"
  attribute {
    name = "id"
    type = "S"
  }
  point_in_time_recovery {
    enabled = true
  }
}
resource "aws_iam_role" "this" {
  name = "${var.name}-failover"
  assume_role_policy = jsonencode({
    Version = "2012-10-17", Statement = [{
      Effect = "Allow", Principal = {
        Service = "lambda.amazonaws.com"
      }, Action = "sts:AssumeRole"
    }]
  })
}
resource "aws_iam_role_policy_attachment" "logs" {
  role       = aws_iam_role.this.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}
resource "aws_iam_role_policy" "this" {
  role = aws_iam_role.this.id
  policy = jsonencode({
    Version = "2012-10-17", Statement = [
    {
      Effect = "Allow", Action = ["dynamodb:GetItem", "dynamodb:UpdateItem"], Resource = [aws_dynamodb_table.control.arn]
    },
    {
      Effect = "Allow", Action = ["rds:DescribeGlobalClusters"], Resource = "*"
    },
    {
      Effect = "Allow", Action = ["rds:FailoverGlobalCluster"], Resource = [
      "arn:aws:rds::${data.aws_caller_identity.current.account_id}:global-cluster:${var.global_cluster}",
      var.primary_cluster_arn, var.secondary_cluster_arn]
    }
    ]
  })
}
resource "aws_cloudwatch_log_group" "this" {
  name              = "/aws/lambda/${var.name}-failover"
  retention_in_days = 90
}
resource "aws_lambda_function" "this" {
  function_name                  = "${var.name}-failover"
  filename                       = var.lambda_zip
  source_code_hash               = filebase64sha256(var.lambda_zip)
  role                           = aws_iam_role.this.arn
  runtime                        = "python3.12"
  handler                        = "atlas.failover.handler"
  memory_size                    = 256
  timeout                        = 50
  reserved_concurrent_executions = 1
  environment {
    variables = {
      CONTROL_TABLE         = aws_dynamodb_table.control.name
      GLOBAL_CLUSTER        = var.global_cluster
      PRIMARY_CLUSTER_ARN   = var.primary_cluster_arn
      SECONDARY_CLUSTER_ARN = var.secondary_cluster_arn
      PRIMARY_URL           = var.primary_url
      SECONDARY_URL         = var.secondary_url
    }
  }
  depends_on = [aws_iam_role_policy.this, aws_iam_role_policy_attachment.logs, aws_cloudwatch_log_group.this]
}
resource "aws_cloudwatch_event_rule" "this" {
  name                = "${var.name}-failover-check"
  schedule_expression = "rate(1 minute)"
  state               = var.enabled ? "ENABLED" : "DISABLED"
}
resource "aws_cloudwatch_event_target" "this" {
  rule = aws_cloudwatch_event_rule.this.name
  arn  = aws_lambda_function.this.arn
}
resource "aws_lambda_permission" "this" {
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.this.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.this.arn
}
