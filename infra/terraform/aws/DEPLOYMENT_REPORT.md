# AWS infrastructure deployment report

Date: 2026-10-29. **Infrastructure applied and verified. Application deployment
has not started.** Existing local Docker services and PostgreSQL data were not
modified or queried for this deployment.

## Authorization and safeguards

The user explicitly approved the reviewed 12-resource saved plan. Before apply:

- STS verified account **598387452073**, caller
  `arn:aws:iam::598387452073:user/platform-lab-admin`.
- Profile **platform-lab**, compute region **us-east-2**.
- SHA-256 verified against the approved digest:
  `dd196ee778b8e77f6d37339516078e0948c22c47e87954c9b15df988477c1fa8`.
- The saved plan was inspected again: 12 creates, no changes/deletes, correct
  account/profile/region, USD 25 monthly budget and approved recipient.
- Applied exactly `terraform apply -input=false -no-color review.tfplan` using
  Terraform 1.17.1 and AWS provider 6.71.0, with AWS_PROFILE=platform-lab.
  No replacement plan, permission changes, or security-policy workaround was used.

**Apply result: 12 added, 0 changed, 0 destroyed. Terraform reported no errors.**

## All Terraform-managed resources created

| Terraform address | Created ID / name |
| --- | --- |
| `aws_budgets_budget.monthly[0]` | `598387452073:platform-engineering-lab-account-monthly` |
| `aws_iam_instance_profile.lab` | `platform-engineering-lab-c47ac8d72882e0643c077261c6` |
| `aws_iam_role.instance` | `platform-engineering-lab-3e3e5c4ef933ec20c00356e1c8` |
| `aws_iam_role_policy.session_manager` | `platform-engineering-lab-3e3e5c4ef933ec20c00356e1c8:session-manager-only` |
| `aws_instance.lab` | `i-0ae38585502b57a05` |
| `aws_internet_gateway.lab` | `igw-0aeb2dfbc8e9245ac` |
| `aws_route_table.public` | `rtb-0ad3b03e8360e47e3` |
| `aws_route_table_association.public` | `rtbassoc-039201b5b410240c2` |
| `aws_security_group.lab` | `sg-060e335a3662e747b` |
| `aws_subnet.public` | `subnet-0b8d5fa7c20dfd096` |
| `aws_vpc.lab` | `vpc-0e2124bd3968ce471` |
| `aws_vpc_security_group_egress_rule.https` | `sgr-09100096433a30bae` |

## Nested and automatically created supporting objects

| Object | ID |
| --- | --- |
| Encrypted root EBS volume | `vol-03ed5c59de967fd43` |
| EC2 primary network interface | `eni-01d248669d8bd8102` |
| VPC default network ACL | `acl-06cb51bf250172e0f` |
| VPC default route table | `rtb-067b887833f9da978` |
| VPC default security group | `sg-0d2405c0220715eac` |

These are supporting objects created by the approved EC2/VPC resources, not
additional Terraform resource addresses. The instance uses the custom closed
security group, not the VPC default security group. Encryption uses AWS-managed
EBS KMS protection; no custom KMS key resource was provisioned.

## EC2 and SSM verification

- Instance **i-0ae38585502b57a05**: running, `t3.small`, `us-east-2a`.
- AMI: `ami-0c7dc2f4e28e6e66e` (Amazon Linux 2023 x86_64).
- EC2 instance, system, and attached-EBS reachability checks: **OK**.
- Root storage: 30 GiB **encrypted gp3**, in use, 3,000 IOPS / 125 MiB/s.
- IMDSv2 required with hop limit 1; metadata settings applied.
- Live security group: **no inbound rules**, only TCP 443 outbound.
- SSM agent **3.3.5390.0**, PingStatus **Online**.
- An actual Session Manager shell connected successfully to
  `ip-10-42-1-75.us-east-2.compute.internal`. No SSH ingress was enabled.
- Cloud-init status **done**, finishing at 2026-10-29 18:33:53 UTC.
- Remote Docker and SSM services both **active**.
- Docker Engine **25.0.18**, Compose **v5.1.1**, Buildx **v0.38.1** installed.
- Bootstrap marker present; Compose/Buildx checksum checks passed in cloud-init.
- Remote `docker ps -a` returned no containers: no application images or services
  were deployed during verification. The verification SSM shell was closed.

Reconnection command (with the Session Manager plugin available on PATH):

```bash
aws ssm start-session --profile platform-lab --region us-east-2 --target i-0ae38585502b57a05
```

## Budget notification verification

Created `platform-engineering-lab-account-monthly` in account 598387452073:

- Account-wide monthly **USD 25** cost budget.
- Exactly one notification: **ACTUAL**, threshold **100%**, **GREATER_THAN**.
- Exact email subscriber **anand.sandhinti@mandsconsulting.com** verified using
  `DescribeSubscribersForNotification` with an explicit PERCENTAGE selector.
- Notification state **OK** at verification.
- Credits and refunds excluded, so promotional credits do not mask usage costs.
- No forecast notification. No test spend or test email was triggered; inbox
  delivery is not verified. Alerts are not an automated spending cap.

## Errors and verification notes

No Terraform apply errors or unresolved infrastructure/bootstrap errors were
observed. Initial verification scripts required corrections for two API/state
normalizations: AWS omitted the optional ThresholdType field in the notification
response, and normalized the amount to the string `25.0`. Verification was
completed using the explicit PERCENTAGE subscriber lookup, persisted Terraform
state, and decimal amount comparison. No resource changes were needed.

## State and next phase

Local Terraform state is saved at `infra/terraform/aws/terraform.tfstate`, ignored
by Git and mode **0600**. The saved applied plan and variable file are also private.
Back up state securely before future infrastructure work. The applied plan is
historical; generate/review a new plan for future changes.

Stopped at the requested boundary. **Applications have not been deployed.**
The documented Git deployment still needs a reviewed, published source revision;
working-tree deployment files are uncommitted/untracked. No Git push, source
checkout on EC2, container build/start, database initialization, or application
smoke test ran. Runtime application and backup/restore verification remain for
the application deployment phase.
