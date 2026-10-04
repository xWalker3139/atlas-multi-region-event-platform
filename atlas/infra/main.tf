resource "random_password" "db" {
  length = 40
  special = false
}
resource "random_password" "api" {
  length = 48
  special = false
}
module "primary_network" {
  source    = "./modules/network"
  providers = {
    aws = aws.primary
  }
  name      = "${var.name}-primary"
  cidr      = var.primary_cidr
  peer_cidr = var.secondary_cidr
}
module "secondary_network" {
  source    = "./modules/network"
  providers = {
    aws = aws.secondary
  }
  name      = "${var.name}-secondary"
  cidr      = var.secondary_cidr
  peer_cidr = var.primary_cidr
}
resource "aws_vpc_peering_connection" "regions" {
  provider    = aws.primary
  vpc_id      = module.primary_network.vpc_id
  peer_vpc_id = module.secondary_network.vpc_id
  peer_region = var.secondary_region
  tags        = {
    Name = "${var.name}-regions"
  }
}
resource "aws_vpc_peering_connection_accepter" "regions" {
  provider                  = aws.secondary
  vpc_peering_connection_id = aws_vpc_peering_connection.regions.id
  auto_accept               = true
}
resource "aws_route" "primary_peer" {
  provider                  = aws.primary
  count                     = 2
  route_table_id            = module.primary_network.route_table_ids[count.index]
  destination_cidr_block    = var.secondary_cidr
  vpc_peering_connection_id = aws_vpc_peering_connection_accepter.regions.id
}
resource "aws_route" "secondary_peer" {
  provider                  = aws.secondary
  count                     = 2
  route_table_id            = module.secondary_network.route_table_ids[count.index]
  destination_cidr_block    = var.primary_cidr
  vpc_peering_connection_id = aws_vpc_peering_connection_accepter.regions.id
}
module "database" {
  source                  = "./modules/database"
  providers               = {
    aws.primary = aws.primary, aws.secondary = aws.secondary
  }
  name                    = var.name
  engine_version          = var.engine_version
  instance_class          = var.instance_class
  password                = random_password.db.result
  primary_subnets         = module.primary_network.subnet_ids
  secondary_subnets       = module.secondary_network.subnet_ids
  primary_sg              = module.primary_network.database_sg
  secondary_sg            = module.secondary_network.database_sg
  deletion_protection     = var.deletion_protection
}
module "primary" {
  source          = "./modules/regional"
  providers       = {
    aws = aws.primary
  }
  name            = var.name
  role            = "primary"
  domain          = var.domain
  zone_id         = var.zone_id
  lambda_zip      = var.lambda_zip
  db_host         = module.database.writer_endpoint
  db_password     = random_password.db.result
  api_token       = random_password.api.result
  regions         = "${var.primary_region},${var.secondary_region}"
  subnet_ids      = module.primary_network.subnet_ids
  lambda_sg       = module.primary_network.lambda_sg
}
module "secondary" {
  source          = "./modules/regional"
  providers       = {
    aws = aws.secondary
  }
  name            = var.name
  role            = "secondary"
  domain          = var.domain
  zone_id         = var.zone_id
  lambda_zip      = var.lambda_zip
  db_host         = module.database.writer_endpoint
  db_password     = random_password.db.result
  api_token       = random_password.api.result
  regions         = "${var.primary_region},${var.secondary_region}"
  subnet_ids      = module.secondary_network.subnet_ids
  lambda_sg       = module.secondary_network.lambda_sg
}
module "failover" {
  source                = "./modules/failover"
  providers             = {
    aws = aws.secondary
  }
  name                  = var.name
  enabled               = var.supervisor_enabled
  lambda_zip            = var.lambda_zip
  global_cluster        = module.database.global_id
  primary_cluster_arn   = module.database.primary_arn
  secondary_cluster_arn = module.database.secondary_arn
  primary_url           = module.primary.api_url
  secondary_url         = module.secondary.api_url
}
module "backup" {
  source = "./modules/backup"
  providers = {
    aws.primary = aws.primary, aws.secondary = aws.secondary
  }
  name = var.name
  primary_cluster_arn = module.database.primary_arn
  secondary_cluster_arn = module.database.secondary_arn
}
