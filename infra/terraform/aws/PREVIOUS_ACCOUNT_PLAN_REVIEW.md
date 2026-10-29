> Historical review for the previous AWS account. Superseded for Phase 1; do not use for deployment approval.

# Infrastructure plan review

Review date: 2026-10-29. Status: **proposed only; not applied**.

## Scope

The authenticated default AWS CLI profile targets `us-east-1`. The local,
ignored `terraform.tfvars` pins the verified account ID and AWS-published
AL2023 x86_64 AMI `ami-041607313c6a2a618`. No credentials are embedded in
Terraform, user data, scripts, or this report.

The saved local plan is `review.tfplan` (ignored, mode 0600). Inspect it with
`terraform show review.tfplan` from this directory. Regenerate it if configuration,
inputs, provider versions, account, or region change. Do not apply a stale plan.

Saved plan SHA-256:

```text
577e7f46bec9839b15960850acf5838d0e647b0994b2593241c21f025b44fecf
```

## Proposed resources

**11 additions, 0 changes, 0 deletions** with the current inputs:

| Resource | Proposed behavior |
| --- | --- |
| VPC | Dedicated `10.42.0.0/16`, DNS enabled |
| Subnet | `10.42.1.0/24`, `us-east-1a` |
| Internet gateway | Outbound internet connectivity |
| Route table | Default IPv4 route through the gateway |
| Route association | Public subnet associated with that table |
| Security group | No inbound rules |
| Egress rule | TCP 443 only to the internet |
| IAM role | EC2 trust only |
| Inline role policy | Five SSM registration/channel actions, no application AWS access |
| Instance profile | Attaches that role to the instance |
| EC2 instance | `t3.small`, one dynamic IPv4, 30 GiB encrypted gp3 root disk |

The root EBS disk is part of the EC2 resource, not a separate Terraform resource.
AWS also creates implicit default VPC objects. No NAT gateway, EIP, load balancer,
RDS, EKS, S3 bucket, or paid private endpoint is requested. The optional account-wide
Budget is **disabled** because no recipient was supplied. Configure `budget_email`
and regenerate the plan to add it, bringing the managed-resource count to 12.

## Security and cost review

- No public service ingress or SSH. User access is through Session Manager.
- Container HTTP ports bind only to EC2 loopback; PostgreSQL has no published port.
- Instance metadata requires IMDSv2 with hop limit 1.
- No keys or application passwords in state/user data. EC2 generates `.env` once.
- Docker Compose v5.1.1 and Buildx v0.38.1 downloads have pinned official SHA-256
  digests. The AWS provider is locked to 6.71.0.
- CPU credit mode is standard; surplus-credit billing is disabled.
- Base full-month on-demand estimate is **$21.23** before credits/tax/extra transfer.
- EC2 instance-type eligibility was reported, but account Free Tier/credit
  eligibility could not be established. Budget alerts are optional, delayed
  notifications, not a hard spending limit.
- Replacement or destroy deletes all EC2-root-disk data, including Docker volumes.
  Take and verify an off-host backup before separately authorizing destruction.

## Validation and limits

- Terraform 1.17.1: `fmt -check`, `init`, `validate`, and authenticated `plan`.
- Two mocked command=plan tests cover secure defaults and optional Budget alerts.
- AWS Compose merge checked without starting containers: health ordering, separate
  PostgreSQL volume, no PostgreSQL port, and loopback application ports.
- Shell scripts pass `bash -n`; both deployment guards reject this workstation.
- Provider startup required approved execution outside the network/process
  sandbox. No workaround weakened the infrastructure configuration.

No `terraform apply` or `terraform destroy` has run. No AWS resources were created
or modified, and no local service was restarted or reconfigured. EC2 bootstrap,
SSM connectivity, AWS-hosted application startup, backups/restoration, account
quotas/capacity, and apply-time authorization remain unverified until a separately
approved deployment. The deployment code must also be committed and published
before the EC2 Git checkout can retrieve it. Runtime updates use the deployment
script and do not require rebuilding the infrastructure.
