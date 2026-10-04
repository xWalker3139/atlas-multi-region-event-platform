output "endpoint" {
  value = "https://${var.domain}"
}
output "primary_api" {
  value = module.primary.api_url
}
output "secondary_api" {
  value = module.secondary.api_url
}
output "global_cluster" {
  value = module.database.global_id
}
output "writer_endpoint" {
  value = module.database.writer_endpoint
}
output "primary_cluster_arn" {
  value = module.database.primary_arn
}
output "secondary_cluster_arn" {
  value = module.database.secondary_arn
}
output "api_secret_arn" {
  value = module.primary.api_secret_arn
}
output "primary_migration_function" {
  value = module.primary.migration_function
}
output "secondary_migration_function" {
  value = module.secondary.migration_function
}
output "primary_queue" {
  value = module.primary.queue_url
}
output "secondary_queue" {
  value = module.secondary.queue_url
}
output "primary_archive" {
  value = module.primary.archive_bucket
}
output "secondary_archive" {
  value = module.secondary.archive_bucket
}
output "primary_database_sg" {
  value = module.primary_network.database_sg
}
output "secondary_database_sg" {
  value = module.secondary_network.database_sg
}
