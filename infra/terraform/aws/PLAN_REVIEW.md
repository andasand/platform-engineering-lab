# Applied Ohio infrastructure plan record

Status: **explicitly approved, applied, and infrastructure verified**.
Review/apply date: 2026-10-29. Terraform created 12 resources with no changes or
deletions. Applications have not been deployed. See [the deployment report](DEPLOYMENT_REPORT.md)
for all created IDs, live checks, and verification notes.

## Verified deployment target

- AWS profile: `platform-lab`.
- Verified STS account: **598387452073**.
- Caller: `arn:aws:iam::598387452073:user/platform-lab-admin`.
- Compute region: **us-east-2 (Ohio)**.
- Selected availability zone: **us-east-2a**.
- Local `terraform.tfvars` explicitly pins the profile, account allowlist, region,
  and regional AMI. It is ignored by Git and mode 0600. No previous-account
  credentials were used.

The us-east-1 SCP-denied plans are historical failed artifacts. No policy was
changed or removed. Ohio availability-zone and AMI reads succeeded normally.
The separate global billing provider uses us-east-1 only for optional Budgets,
with the same profile and account restriction; compute remains entirely in Ohio.

## Saved plan and exact resources

Terraform 1.17.1, locked AWS provider 6.71.0. Saved plan: `review.tfplan`
(ignored, mode 0600). SHA-256:

```text
dd196ee778b8e77f6d37339516078e0948c22c47e87954c9b15df988477c1fa8
```

**12 additions, 0 changes, 0 deletions.** The plan JSON is complete and was
checked against the exact expected resource-address allowlist.

| Address | Planned resource |
| --- | --- |
| `aws_vpc.lab` | VPC `10.42.0.0/16`, DNS support/hostnames enabled |
| `aws_subnet.public` | Subnet `10.42.1.0/24` in `us-east-2a` |
| `aws_internet_gateway.lab` | Internet gateway for outbound access |
| `aws_route_table.public` | Default IPv4 route through the gateway |
| `aws_route_table_association.public` | Associate subnet with route table |
| `aws_security_group.lab` | No inbound rules |
| `aws_vpc_security_group_egress_rule.https` | Outbound TCP 443 only |
| `aws_iam_role.instance` | Trust only `ec2.amazonaws.com` |
| `aws_iam_role_policy.session_manager` | Five SSM registration/channel actions |
| `aws_iam_instance_profile.lab` | Attach the instance role |
| `aws_instance.lab` | `t3.small`, dynamic public IPv4, encrypted 30 GiB gp3 root disk |
| `aws_budgets_budget.monthly[0]` | Account-wide USD 25 monthly Budget with one 100% actual-spend email alert |

EBS is nested in the instance resource. AWS creates implicit default VPC objects
as usual; existing VPCs and defaults are not managed or modified by this plan.
No NAT, EIP, load balancer, RDS, EKS, or paid private endpoint is requested.
The Budget is enabled in the saved plan. All 11 prior infrastructure definitions
were compared against the previous Ohio plan and are identical; only the Budget
resource was added.

## Regional AMI compatibility

Pinned image: **ami-0c7dc2f4e28e6e66e**, obtained from AWS's public regional
`/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64` parameter.
Independent `DescribeImages` verified:

- Name: `al2023-ami-2023.12.20261028.0-kernel-6.18-x86_64`.
- Owner: AWS Amazon Linux account `137112412989`; public and available.
- Architecture x86_64, virtualization HVM, EBS root `/dev/xvda`, UEFI preferred.

These match T3 and the x86_64 Docker Compose/Buildx binaries. Bootstrap uses
AL2023 `dnf` to install Docker/Git/Python, enables Docker/SSM Agent, verifies
pinned Compose/Buildx SHA-256 digests, and configures bounded Docker logs.
Compatibility was reviewed before apply. Boot, cloud-init completion, and installed
Docker/Compose/Buildx versions are now verified in the deployment report.

## Security review

- No inbound security-group rules, no SSH key, and no public application URL.
- A public IPv4/internet route is used for outbound HTTPS instead of a NAT.
- EventFlow 8001, ingestor 8002, Prometheus 9090, and Grafana 3000 bind only to
  EC2 loopback. PostgreSQL has no published host port.
- Administration and Grafana access use SSM shell/port forwarding, with explicit
  `platform-lab` profile and Ohio region in the Terraform command outputs.
- IMDSv2 required; hop limit 1 intentionally prevents bridged applications from
  obtaining instance-role credentials. These applications need no AWS API access.
- The instance role permits only `ssm:UpdateInstanceInformation` and four
  `ssmmessages` channel actions. No application IAM privileges or embedded secrets.
- Root EBS encrypted; standard CPU credit mode avoids surplus-credit billing.
- Application secrets are generated once on the host, root-owned mode 0600,
  outside Terraform state and Git.

A successful plan is not proof of EC2 launch capacity, quotas, apply-time
authorization, SSM connectivity, or runtime application health. Those checks
require the approved deployment. No SCP/IAM changes were made to obtain this plan.

## Five-service deployment readiness

AWS Compose merge checks and all deployment/backup/bootstrap shell syntax passed.
The AWS override runs PostgreSQL 16.15, EventFlow, Security Log Ingestor,
Prometheus, and Grafana, plus the additive one-shot schema initializer.
Persistent Docker volumes hold PostgreSQL, metrics, and Grafana data. Application
updates preserve those volumes and existing host secrets. The deployment script
builds images serially and runs the existing smoke and observability checks.

Session Manager plugin 1.2.896.0 is staged under `/tmp/platform-session-manager/`;
add its `usr/local/sessionmanagerplugin/bin` directory to PATH for CLI tunnels.
The documented Git deployment still requires a published, reviewed source
revision: infrastructure/Compose/deployment files are currently uncommitted or
untracked. Application execution and backup restoration remain untested on AWS;
SSM connectivity and bootstrap are verified. The local Docker deployment and
PostgreSQL records are unchanged.

## Verified Ohio cost estimate

Fresh AWS Pricing API data confirmed Linux `t3.small` in Ohio at $0.0208/hour
and Ohio gp3 storage at $0.08/GB-month. Public IPv4 is $0.005/hour. Python
Decimal calculations, using 730 running hours/month:

| Item | Estimated monthly cost |
| --- | ---: |
| EC2 t3.small | $15.18 |
| 30 GiB gp3, baseline performance | $2.40 |
| One public IPv4 | $3.65 |
| Base total | **$21.23** |

Low-volume transfer is assumed within the account's remaining shared allowance.
Tax, transfer beyond allowances, snapshots/additional storage, and other account
usage can add cost. EBS remains billable while the instance is stopped.

The last authenticated account-plan check reported FREE/ACTIVE, **$100** remaining,
and account-plan expiration **2027-04-29 15:42:17 UTC**. That balance represents
approximately **4.71 months** of eligible base charges before other usage, credit
exclusions, or expiration. Confirm individual credit terms in Billing.

Sources: [EBS pricing](https://aws.amazon.com/ebs/pricing/),
[IPv4 pricing](https://aws.amazon.com/vpc/pricing/), authenticated AWS Pricing
responses for Ohio, and the prior authenticated Free Tier account-plan response.

## Enabled $25 Budget

The saved plan includes `aws_budgets_budget.monthly[0]`, account-wide, monthly,
USD 25. It contains exactly one notification: **ACTUAL**, **100%**, comparison
**GREATER_THAN**. The exact user-specified recipient is stored in ignored local
`terraform.tfvars` and the private saved plan; it was verified against the request.
No forecast notification remains.

Credits and refunds remain excluded from usage costs, so promotional credits
do not hide spending from this alert. The dedicated billing provider uses the
same profile/account allowlist and the global endpoint in us-east-1; compute
remains in Ohio. Budget monitoring does not add an AWS Budgets charge. Alerts
are delayed notifications, not a hard spending cap. The budget and notification configuration have now been
created and verified; email inbox delivery was not triggered or tested.

## Validation and approval boundary

- `terraform fmt -check`: passed.
- `terraform init`: passed, provider lock reused.
- `terraform validate`: passed.
- Both mocked plan-only tests: passed (secure defaults and enabled $25 actual-spend Budget).
- Live Ohio `terraform plan`: passed, 12 creates / 0 updates / 0 deletes.
- Saved plan JSON: correct profile/account/region, exact resources, security and
  storage settings, standard CPU credits, and bootstrap content verified.
- AWS Compose merge and shell syntax: passed without starting local containers.

**The explicitly approved saved plan was applied successfully.** No destroy ran.
Stopped before application deployment as requested. Any future changed region,
AMI, budget recipient, configuration, or input requires a new plan and review;
the applied saved plan is historical. Replacement/destroy deletes the root disk
and all host Docker data; verify off-host backups first.
