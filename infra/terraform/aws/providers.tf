provider "aws" {
  profile             = var.aws_profile
  region              = var.aws_region
  allowed_account_ids = var.allowed_account_ids

  default_tags {
    tags = {
      Project   = var.project_name
      ManagedBy = "Terraform"
    }
  }
}

# AWS Budgets is an account-wide billing service. Compute remains in Ohio.
provider "aws" {
  alias               = "billing"
  profile             = var.aws_profile
  region              = "us-east-1"
  allowed_account_ids = var.allowed_account_ids
}
