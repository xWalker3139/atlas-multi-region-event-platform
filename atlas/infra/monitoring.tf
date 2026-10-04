resource "aws_cloudwatch_metric_alarm" "rpo" {
  provider            = aws.secondary
  alarm_name          = "${var.name}-rpo-lag-over-one-second"
  namespace           = "AWS/RDS"
  metric_name         = "AuroraGlobalDBRPOLag"
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 2
  threshold           = 1000
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "missing"
  dimensions          = {
    DBClusterIdentifier = "${var.name}-secondary"
  }
}
resource "aws_cloudwatch_dashboard" "this" {
  provider       = aws.secondary
  dashboard_name = "${var.name}-operations"
  dashboard_body = jsonencode({
    widgets = [
    {
      type = "metric", x = 0, y = 0, width = 12, height = 6, properties = {
        title = "Global DB RPO / replication lag (ms)", region = var.secondary_region,
        period = 60, stat = "Maximum", view = "timeSeries", metrics = [
        ["AWS/RDS", "AuroraGlobalDBRPOLag", "DBClusterIdentifier", "${var.name}-secondary"],
        ["AWS/RDS", "AuroraGlobalDBReplicationLag", "DBClusterIdentifier", "${var.name}-secondary"]
        ]
      }
    },
    {
      type = "metric", x = 12, y = 0, width = 12, height = 6, properties = {
        title = "Queue age and dead letters", region = var.secondary_region, period = 60, stat = "Maximum",
        view = "timeSeries", metrics = [
        ["AWS/SQS", "ApproximateAgeOfOldestMessage", "QueueName", "${var.name}-secondary-events"],
        ["AWS/SQS", "ApproximateNumberOfMessagesVisible", "QueueName", "${var.name}-secondary-dlq"]
        ]
      }
    },
    {
      type = "metric", x = 0, y = 6, width = 12, height = 6, properties = {
        title = "Primary API errors / throttles", region = var.primary_region, period = 60, stat = "Sum",
        view = "timeSeries", metrics = [
        ["AWS/Lambda", "Errors", "FunctionName", "${var.name}-primary-api"],
        ["AWS/Lambda", "Throttles", "FunctionName", "${var.name}-primary-api"]
        ]
      }
    },
    {
      type = "log", x = 12, y = 6, width = 12, height = 6, properties = {
        title = "Failover decisions", region = var.secondary_region,
        query = "SOURCE '/aws/lambda/${var.name}-failover' | fields @timestamp, action, failures | filter ispresent(action) | sort @timestamp desc | limit 50"
      }
    }
    ]
  })
}
