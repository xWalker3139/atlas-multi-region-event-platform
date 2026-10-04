terraform {
  required_version = ">= 1.10, < 2.0"
  required_providers {
    aws    = {
      source = "hashicorp/aws", version = "~> 6.21"
    }
    random = {
      source = "hashicorp/random", version = "~> 3.7"
    }
  }
  # Configure an encrypted S3 backend via -backend-config=backend.hcl.
  backend "s3" {
  }
}
provider "aws" {
  alias  = "primary"
  region = var.primary_region
  default_tags {
    tags = {
      Project = var.name, ManagedBy = "Terraform"
    }
  }
}
provider "aws" {
  alias  = "secondary"
  region = var.secondary_region
  default_tags {
    tags = {
      Project = var.name, ManagedBy = "Terraform"
    }
  }
}
