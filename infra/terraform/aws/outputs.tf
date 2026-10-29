output "instance_id" {
  value       = aws_instance.lab.id
  description = "SSM target. There is intentionally no public application URL."
}

output "ami_id" {
  value       = aws_instance.lab.ami
  description = "Resolved AL2023 AMI; pin this in terraform.tfvars before approving future plans."
}

output "region" {
  value = var.aws_region
}

output "ssm_session_command" {
  value = "aws ssm start-session${var.aws_profile == null ? "" : " --profile ${var.aws_profile}"} --region ${var.aws_region} --target ${aws_instance.lab.id}"
}

output "grafana_forward_command" {
  value = "aws ssm start-session${var.aws_profile == null ? "" : " --profile ${var.aws_profile}"} --region ${var.aws_region} --target ${aws_instance.lab.id} --document-name AWS-StartPortForwardingSession --parameters '{\"portNumber\":[\"3000\"],\"localPortNumber\":[\"13000\"]}'"
}

output "budget_enabled" {
  value = var.budget_email != ""
}
