variable "aws_profile" {
  description = "Explicit AWS CLI profile. Set platform-lab for Phase 1; null uses the normal AWS credential chain."
  type        = string
  default     = null
  validation {
    condition     = var.aws_profile == null ? true : can(regex("^[A-Za-z0-9_.-]+$", var.aws_profile))
    error_message = "Use a profile name containing letters, digits, underscores, periods, or hyphens."
  }
}

variable "aws_region" {
  description = "Compute region; Ohio is permitted for the reviewed deployment."
  type        = string
  default     = "us-east-2"
}

variable "allowed_account_ids" {
  description = "Optional account allowlist to prevent deployment into the wrong account. Uses the normal AWS credential chain."
  type        = list(string)
  default     = []
  validation {
    condition     = alltrue([for id in var.allowed_account_ids : can(regex("^[0-9]{12}$", id))])
    error_message = "Each AWS account ID must contain exactly 12 digits."
  }
}

variable "project_name" {
  description = "Unique prefix and Project tag for these NEW resources."
  type        = string
  default     = "platform-engineering-lab"
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{2,31}$", var.project_name))
    error_message = "Use 3-32 lowercase letters, digits, or hyphens, starting with a letter."
  }
}

variable "instance_type" {
  description = "2 GiB minimum recommended; x86 burstable instances only. Standard CPU credits avoid surplus-credit charges."
  type        = string
  default     = "t3.small"
  validation {
    condition     = contains(["t3.small", "t3.medium", "t3a.small", "t3a.medium"], var.instance_type)
    error_message = "Choose a supported x86 t3/t3a small or medium instance."
  }
}

variable "ami_id" {
  description = "Pin the reviewed Amazon Linux 2023 x86_64 AMI ID. Null discovers the current AWS-published AMI during plan."
  type        = string
  default     = null
  validation {
    condition     = var.ami_id == null ? true : can(regex("^ami-[0-9a-f]+$", var.ami_id))
    error_message = "ami_id must be null or an AMI ID."
  }
}

variable "root_volume_gib" {
  description = "Encrypted gp3 root disk containing Docker images and ALL persistent Docker volumes."
  type        = number
  default     = 30
  validation {
    condition     = var.root_volume_gib >= 30 && var.root_volume_gib <= 100 && floor(var.root_volume_gib) == var.root_volume_gib
    error_message = "Use an integer disk size between 30 and 100 GiB."
  }
}

variable "budget_email" {
  description = "Optional cost-alert recipient. Empty disables creation of the account-wide AWS Budget. No email is sent during plan."
  type        = string
  default     = ""
  validation {
    condition     = var.budget_email == "" || can(regex("^[^ @]+@[^ @]+\\.[^ @]+$", var.budget_email))
    error_message = "Use a valid email address, or an empty string to disable the budget."
  }
}

variable "monthly_budget_usd" {
  description = "Account-wide monthly alert threshold; alerts are NOT a spending cap."
  type        = number
  default     = 25
  validation {
    condition     = var.monthly_budget_usd > 0
    error_message = "The monthly budget must be positive."
  }
}
