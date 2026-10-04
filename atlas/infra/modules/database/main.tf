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
variable "engine_version" {
  type = string
}
variable "instance_class" {
  type = string
}
variable "password" {
  type = string
  sensitive = true
}
variable "primary_subnets" {
  type = list(string)
}
variable "secondary_subnets" {
  type = list(string)
}
variable "primary_sg" {
  type = string
}
variable "secondary_sg" {
  type = string
}
variable "deletion_protection" {
  type = bool
}
resource "aws_kms_key" "primary" {
  provider = aws.primary
  description = "Atlas primary Aurora encryption"
  enable_key_rotation = true
  deletion_window_in_days = 30
}
resource "aws_kms_key" "secondary" {
  provider = aws.secondary
  description = "Atlas secondary Aurora encryption"
  enable_key_rotation = true
  deletion_window_in_days = 30
}
resource "aws_rds_global_cluster" "this" {
  provider            = aws.primary
  global_cluster_identifier = "${var.name}-global"
  engine              = "aurora-postgresql"
  engine_version      = var.engine_version
  database_name       = "atlas"
  storage_encrypted   = true
  deletion_protection = var.deletion_protection
}
resource "aws_db_subnet_group" "primary" {
  provider   = aws.primary
  name       = "${var.name}-primary"
  subnet_ids = var.primary_subnets
}
resource "aws_db_subnet_group" "secondary" {
  provider   = aws.secondary
  name       = "${var.name}-secondary"
  subnet_ids = var.secondary_subnets
}
resource "aws_rds_cluster_parameter_group" "primary" {
  provider = aws.primary
  name     = "${var.name}-primary-pg16"
  family   = "aurora-postgresql16"
  parameter {
    name = "rds.force_ssl"
    value = "1"
    apply_method = "pending-reboot"
  }
  parameter {
    name = "rds.global_db_rpo"
    value = "20"
    apply_method = "pending-reboot"
  }
}
resource "aws_rds_cluster_parameter_group" "secondary" {
  provider = aws.secondary
  name     = "${var.name}-secondary-pg16"
  family   = "aurora-postgresql16"
  parameter {
    name = "rds.force_ssl"
    value = "1"
    apply_method = "pending-reboot"
  }
  parameter {
    name = "rds.global_db_rpo"
    value = "20"
    apply_method = "pending-reboot"
  }
}
resource "aws_rds_cluster" "primary" {
  provider                = aws.primary
  cluster_identifier      = "${var.name}-primary"
  global_cluster_identifier = aws_rds_global_cluster.this.id
  engine                  = "aurora-postgresql"
  engine_version          = var.engine_version
  database_name           = "atlas"
  master_username         = "atlas_admin"
  master_password         = var.password
  kms_key_id              = aws_kms_key.primary.arn
  db_subnet_group_name    = aws_db_subnet_group.primary.name
  vpc_security_group_ids  = [var.primary_sg]
  db_cluster_parameter_group_name = aws_rds_cluster_parameter_group.primary.name
  storage_encrypted       = true
  backup_retention_period = 14
  deletion_protection     = var.deletion_protection
  skip_final_snapshot     = false
  final_snapshot_identifier = "${var.name}-primary-final"
  enabled_cloudwatch_logs_exports = ["postgresql"]
  lifecycle {
    ignore_changes = [global_cluster_identifier]
  }
}
resource "aws_rds_cluster" "secondary" {
  provider                = aws.secondary
  cluster_identifier      = "${var.name}-secondary"
  global_cluster_identifier = aws_rds_global_cluster.this.id
  engine                  = "aurora-postgresql"
  engine_version          = var.engine_version
  db_subnet_group_name    = aws_db_subnet_group.secondary.name
  vpc_security_group_ids  = [var.secondary_sg]
  db_cluster_parameter_group_name = aws_rds_cluster_parameter_group.secondary.name
  storage_encrypted       = true
  kms_key_id              = aws_kms_key.secondary.arn
  backup_retention_period = 14
  deletion_protection     = var.deletion_protection
  skip_final_snapshot     = false
  final_snapshot_identifier = "${var.name}-secondary-final"
  enabled_cloudwatch_logs_exports = ["postgresql"]
  depends_on              = [aws_rds_cluster_instance.primary]
  lifecycle {
    ignore_changes = [global_cluster_identifier]
  }
}
resource "aws_rds_cluster_instance" "primary" {
  provider           = aws.primary
  count              = 2
  identifier         = "${var.name}-primary-${count.index}"
  cluster_identifier = aws_rds_cluster.primary.id
  instance_class     = var.instance_class
  engine             = "aurora-postgresql"
  engine_version     = var.engine_version
  publicly_accessible = false
}
resource "aws_rds_cluster_instance" "secondary" {
  provider           = aws.secondary
  count              = 2
  identifier         = "${var.name}-secondary-${count.index}"
  cluster_identifier = aws_rds_cluster.secondary.id
  instance_class     = var.instance_class
  engine             = "aurora-postgresql"
  engine_version     = var.engine_version
  publicly_accessible = false
}
output "global_id" {
  value = aws_rds_global_cluster.this.id
}
data "aws_rds_global_cluster" "ready" {
  provider = aws.primary
  identifier = aws_rds_global_cluster.this.id
  depends_on = [aws_rds_cluster_instance.primary, aws_rds_cluster_instance.secondary]
}
output "writer_endpoint" {
  value = data.aws_rds_global_cluster.ready.endpoint
}
output "primary_arn" {
  value = aws_rds_cluster.primary.arn
}
output "secondary_arn" {
  value = aws_rds_cluster.secondary.arn
}
