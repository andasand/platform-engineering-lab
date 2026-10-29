mock_provider "aws" {}
mock_provider "aws" {
  alias = "billing"
}

run "secure_defaults" {
  command = plan
  variables {
    ami_id       = "ami-0123456789abcdef0"
    budget_email = ""
  }
  override_data {
    target = data.aws_availability_zones.available
    values = { names = ["us-east-2a"] }
  }
  assert {
    condition     = length(aws_security_group.lab.ingress) == 0
    error_message = "The instance must have no inbound security-group rules."
  }
  assert {
    condition     = aws_instance.lab.root_block_device[0].encrypted && aws_instance.lab.root_block_device[0].volume_type == "gp3"
    error_message = "Root storage must be encrypted gp3."
  }
  assert {
    condition     = aws_instance.lab.metadata_options[0].http_tokens == "required" && aws_instance.lab.metadata_options[0].http_put_response_hop_limit == 1
    error_message = "Require IMDSv2 and prevent container access to instance credentials."
  }
  assert {
    condition     = aws_instance.lab.credit_specification[0].cpu_credits == "standard"
    error_message = "Do not enable billable unlimited surplus CPU credits."
  }
  assert {
    condition     = aws_vpc_security_group_egress_rule.https.from_port == 443 && aws_vpc_security_group_egress_rule.https.to_port == 443
    error_message = "Internet egress must be restricted to HTTPS."
  }
  assert {
    condition     = length(aws_budgets_budget.monthly) == 0
    error_message = "No budget recipient means no budget resource."
  }
  assert {
    condition     = length(jsondecode(aws_iam_role_policy.session_manager.policy).Statement[0].Action) == 5
    error_message = "Instance IAM must remain limited to agent registration and SSM session channels."
  }
}

run "optional_budget" {
  command = plan
  variables {
    ami_id       = "ami-0123456789abcdef0"
    budget_email = "lab-owner@example.com"
  }
  override_data {
    target = data.aws_availability_zones.available
    values = { names = ["us-east-2a"] }
  }
  assert {
    condition     = length(aws_budgets_budget.monthly) == 1 && length(aws_budgets_budget.monthly[0].notification) == 1
    error_message = "A configured recipient must receive exactly one actual-spend alert."
  }
  assert {
    condition     = aws_budgets_budget.monthly[0].limit_amount == "25" && !aws_budgets_budget.monthly[0].cost_types[0].include_credit
    error_message = "The budget must be $25 before promotional credits offset costs."
  }
  assert {
    condition = alltrue([for notification in aws_budgets_budget.monthly[0].notification :
      notification.notification_type == "ACTUAL" && notification.threshold == 100 &&
      notification.threshold_type == "PERCENTAGE" &&
      notification.subscriber_email_addresses == toset(["lab-owner@example.com"])
    ])
    error_message = "Alert must use 100% of actual spending and the configured email."
  }
}
