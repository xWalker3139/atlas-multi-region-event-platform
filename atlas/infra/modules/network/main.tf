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
variable "cidr" {
  type = string
}
variable "peer_cidr" {
  type = string
}
data "aws_region" "current" {
}
data "aws_availability_zones" "available" {
  state = "available"
}
resource "aws_vpc" "this" {
  cidr_block           = var.cidr
  enable_dns_support   = true
  enable_dns_hostnames = true
  tags                 = {
    Name = var.name
  }
}
resource "aws_subnet" "private" {
  count             = 2
  vpc_id            = aws_vpc.this.id
  cidr_block        = cidrsubnet(var.cidr, 8, count.index)
  availability_zone = data.aws_availability_zones.available.names[count.index]
  tags              = {
    Name = "${var.name}-${count.index}"
  }
}
resource "aws_route_table" "private" {
  count  = 2
  vpc_id = aws_vpc.this.id
}
resource "aws_route_table_association" "private" {
  count          = 2
  subnet_id      = aws_subnet.private[count.index].id
  route_table_id = aws_route_table.private[count.index].id
}
resource "aws_security_group" "lambda" {
  name   = "${var.name}-lambda"
  vpc_id = aws_vpc.this.id
  egress {
    from_port = 0
    to_port = 0
    protocol = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}
resource "aws_security_group" "db" {
  name   = "${var.name}-db"
  vpc_id = aws_vpc.this.id
  ingress {
    from_port = 5432
    to_port = 5432
    protocol = "tcp"
    cidr_blocks = [var.cidr, var.peer_cidr]
  }
}
resource "aws_security_group" "endpoints" {
  name   = "${var.name}-endpoints"
  vpc_id = aws_vpc.this.id
  ingress {
    from_port = 443
    to_port = 443
    protocol = "tcp"
    security_groups = [aws_security_group.lambda.id]
  }
}
resource "aws_vpc_endpoint" "interface" {
  for_each            = toset(["sqs", "secretsmanager", "logs"])
  vpc_id              = aws_vpc.this.id
  service_name        = "com.amazonaws.${data.aws_region.current.region}.${each.key}"
  vpc_endpoint_type   = "Interface"
  private_dns_enabled = true
  subnet_ids          = aws_subnet.private[*].id
  security_group_ids  = [aws_security_group.endpoints.id]
}
resource "aws_vpc_endpoint" "s3" {
  vpc_id          = aws_vpc.this.id
  service_name    = "com.amazonaws.${data.aws_region.current.region}.s3"
  route_table_ids = aws_route_table.private[*].id
}
output "vpc_id" {
  value = aws_vpc.this.id
}
output "subnet_ids" {
  value = aws_subnet.private[*].id
}
output "route_table_ids" {
  value = aws_route_table.private[*].id
}
output "database_sg" {
  value = aws_security_group.db.id
}
output "lambda_sg" {
  value = aws_security_group.lambda.id
}
