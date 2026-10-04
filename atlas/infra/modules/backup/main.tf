terraform {
  required_providers {
    aws = {
      source = "hashicorp/aws", configuration_aliases = [aws.primary, aws.secondary]
    }
  }
}
variable "name" {
  type = string
}
variable "primary_cluster_arn" {
  type = string
}
variable "secondary_cluster_arn" {
  type = string
}
resource "aws_kms_key" "primary" {
  provider = aws.primary
  description = "Atlas backup vault primary"
  enable_key_rotation = true
  deletion_window_in_days = 30
}
resource "aws_kms_key" "secondary" {
  provider = aws.secondary
  description = "Atlas backup vault secondary"
  enable_key_rotation = true
  deletion_window_in_days = 30
}
resource "aws_backup_vault" "primary" {
  provider    = aws.primary
  name        = "${var.name}-primary-backups"
  kms_key_arn = aws_kms_key.primary.arn
}
resource "aws_backup_vault" "secondary" {
  provider    = aws.secondary
  name        = "${var.name}-secondary-backups"
  kms_key_arn = aws_kms_key.secondary.arn
}
resource "aws_iam_role" "this" {
  provider = aws.primary
  name     = "${var.name}-backup"
  assume_role_policy = jsonencode({
    Version = "2012-10-17", Statement = [{
      Effect = "Allow", Principal = {
        Service = "backup.amazonaws.com"
      }, Action = "sts:AssumeRole"
    }]
  })
}
resource "aws_iam_role_policy_attachment" "this" {
  provider   = aws.primary
  role       = aws_iam_role.this.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSBackupServiceRolePolicyForBackup"
}
resource "aws_backup_plan" "primary" {
  provider = aws.primary
  name     = "${var.name}-primary-daily"
  rule {
    rule_name         = "daily"
    target_vault_name = aws_backup_vault.primary.name
    schedule          = "cron(0 2 * * ? *)"
    start_window      = 60
    completion_window = 360
    lifecycle {
      delete_after = 30
    }
    copy_action {
      destination_vault_arn = aws_backup_vault.secondary.arn
      lifecycle {
        delete_after = 30
      }
    }
  }
}
resource "aws_backup_plan" "secondary" {
  provider = aws.secondary
  name     = "${var.name}-secondary-daily"
  rule {
    rule_name         = "daily"
    target_vault_name = aws_backup_vault.secondary.name
    schedule          = "cron(0 3 * * ? *)"
    start_window      = 60
    completion_window = 360
    lifecycle {
      delete_after = 30
    }
    copy_action {
      destination_vault_arn = aws_backup_vault.primary.arn
      lifecycle {
        delete_after = 30
      }
    }
  }
}
resource "aws_backup_selection" "primary" {
  provider     = aws.primary
  name         = "${var.name}-primary"
  plan_id      = aws_backup_plan.primary.id
  iam_role_arn = aws_iam_role.this.arn
  resources    = [var.primary_cluster_arn]
  depends_on   = [aws_iam_role_policy_attachment.this]
}
resource "aws_backup_selection" "secondary" {
  provider     = aws.secondary
  name         = "${var.name}-secondary"
  plan_id      = aws_backup_plan.secondary.id
  iam_role_arn = aws_iam_role.this.arn
  resources    = [var.secondary_cluster_arn]
  depends_on   = [aws_iam_role_policy_attachment.this]
}
