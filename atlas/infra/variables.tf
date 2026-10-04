variable "name" {
  default = "atlas"
}
variable "primary_region" {
  default = "eu-west-1"
}
variable "secondary_region" {
  default = "eu-central-1"
}
variable "zone_id" {
  type = string
}
variable "domain" {
  type        = string
  description = "Owned Route53 DNS name, e.g. atlas.example.com"
}
variable "engine_version" {
  type        = string
  description = "Same Aurora PostgreSQL 16 version supporting Global DB in both regions (preflight validates)"
}
variable "instance_class" {
  default = "db.r6g.large"
}
variable "lambda_zip" {
  default = "../dist/atlas-lambda.zip"
}
variable "deletion_protection" {
  default = true
}
variable "primary_cidr" {
  default = "10.40.0.0/16"
}
variable "secondary_cidr" {
  default = "10.41.0.0/16"
}
variable "supervisor_enabled" {
  default     = true
  description = "Automatically promotes after three DB-readiness failures from both regional APIs"
}
