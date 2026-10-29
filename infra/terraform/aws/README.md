# AWS deployment for Platform Engineering Lab

This is a single-host learning deployment, not a highly available production
system. The explicitly approved Ohio infrastructure was **applied on 2026-10-29**.
See [the deployment report](DEPLOYMENT_REPORT.md) for all resource IDs and live
verification results. **Applications have not been deployed.** Future changes
require a new reviewed plan and approval.

See [the recorded plan review](PLAN_REVIEW.md) for the current proposed resource
count, validation results, and remaining runtime checks.

## Design and isolation

- One new VPC (`10.42.0.0/16`), public subnet, internet gateway and route table.
- One Amazon Linux 2023 x86_64 EC2 instance, default `t3.small` (2 vCPU, 2 GiB).
- One 30 GiB encrypted gp3 root disk using the AWS-managed EBS encryption key.
- No inbound security-group rules, SSH key, or public application endpoints.
  Only outbound TCP 443 is allowed for SSM, packages, Git, and image downloads.
- IMDSv2 required, metadata hop limit 1, preventing the default bridged containers
  from reaching instance-role credentials. The role allows only registration
  (`ssm:UpdateInstanceInformation`) and the four `ssmmessages` channel actions.
  These actions require `Resource: "*"`; there are no application AWS permissions.
- A dynamic public IPv4 address provides outbound internet access without a NAT
  gateway. A public IP does not bypass the empty inbound security group.
- Docker volumes for PostgreSQL, Prometheus, and Grafana live on the encrypted
  root disk. EventFlow continues to use process memory. Prometheus keeps at most
  15 days / approximately 2 GB of metrics; WAL/head and other data need extra disk.
- No load balancer, RDS, EKS, NAT gateway, EIP, paid VPC endpoints, custom KMS key,
  S3 bucket, or CloudWatch log ingestion is provisioned.

The root `compose.yaml` stays unchanged. The AWS-only
`../../deploy/compose.aws.yaml` override adds PostgreSQL 16.15 with no host port,
makes the database network project-owned, and adds PostgreSQL health ordering.
The five long-running services are PostgreSQL, EventFlow, Security Log Ingestor,
Prometheus, and Grafana; `db-init` is a one-shot additive schema task. Application
ports remain loopback-only: 8001, 8002, 9090, and 3000. Existing dashboards and
scrape configuration are reused. No local records or credentials are copied.

**Do not run the AWS Compose override on your workstation.** Deployment and backup
scripts require root, `/etc/platform-engineering-lab-aws`, and the dedicated
`/opt/platform-engineering-lab` checkout. Their guard rejects the current local lab.

## Account, Free Tier, and expected cost

Read-only checks verified profile **platform-lab**, account **598387452073**,
and compute region **us-east-2 (Ohio)**. AWS reported an active Free plan with
**$100** remaining and account-plan expiration **April 29, 2027** before apply.
The reviewed infrastructure has now been applied; see the deployment report. The earlier us-east-1 EC2 reads were denied by an
Organizations SCP; Ohio availability-zone and AMI reads succeeded.

The regional AMI is pinned to `ami-0c7dc2f4e28e6e66e`, an available AWS-owned
AL2023 x86_64 HVM image with EBS storage, compatible with the T3 instance and
x86 Docker/Compose/Buildx binaries. The [current review](PLAN_REVIEW.md) records
the saved plan and its approval status. Never apply an archived failed plan.

The verified $100 balance represents roughly 4.7 months of the $21.23 base
estimate if all charges are eligible and no other workloads consume credits.
Verify individual credit terms and expiration in Billing; the account-plan
expiration is not a guarantee of every credit's expiration date.
[AWS applies credits to eligible charges](https://docs.aws.amazon.com/awsaccountbilling/latest/aboutv2/useconsolidatedbilling-credits.html).

Observed idle container memory was approximately 985 MiB: Grafana 611 MiB,
Prometheus 140 MiB, ingestor 53 MiB, EventFlow 44 MiB, PostgreSQL 37 MiB. This
excludes the host, Docker, filesystem cache, build-time peaks, and workload growth.
One GiB is too tight. Use `t3.small` initially, build the two application images
serially, and monitor memory. Choose `t3.medium` if builds or runtime OOM. These
are estimates from a light local workload, not a load-test capacity guarantee.

Estimated Linux on-demand costs in **us-east-2 (Ohio)**, 730 running hours/month,
before credits, discounts, tax, backups, or excess transfer:

| Item | Rate | Monthly estimate |
| --- | --- | ---: |
| `t3.small` EC2 | $0.0208/hour, verified through AWS Pricing API | $15.18 |
| 30 GiB gp3 EBS, baseline IOPS/throughput | $0.08/GB-month | $2.40 |
| One in-use public IPv4 | $0.005/hour | $3.65 |
| Low-volume internet transfer | $0 if within unused account-wide allowance | $0.00 |
| **Base total** | | **$21.23/month** |

Other instance types change pricing and capacity. CPU credits are **standard**, avoiding
unlimited surplus-credit charges; CPU can throttle when credits run out.

AWS currently offers 100 GB/month outbound internet transfer across eligible
services/regions, shared with your other workloads. If already used, additional
internet transfer can cost approximately $0.09/GB in this region's first tier.
Image pulls are inbound; dashboard viewing, package traffic responses, backup
downloads, and SSM traffic can contribute outbound usage. Communication among
these containers stays on one host. EBS remains billable while the instance is
stopped; its dynamic public IPv4 is released on stop. Snapshots, additional disk,
transfers above allowances, taxes, and other account resources can add charges,
even when EC2 compute is covered by credits or Free Tier.

Sources: [EC2 T3 prices](https://aws.amazon.com/ec2/instance-types/t3/),
[EBS pricing](https://aws.amazon.com/ebs/pricing/),
[IPv4 pricing](https://aws.amazon.com/vpc/pricing/),
[EC2 transfer pricing](https://aws.amazon.com/ec2/pricing/on-demand/).

Set `budget_email` to create an **account-wide** $25/month AWS Budget:
one email alert at 100% of actual spending. Credits and refunds are excluded
so promotional credits do not hide usage costs. It includes other account spending,
requires billing permissions, and is disabled until you provide a recipient.
No email is sent by `plan`. Basic budget monitoring/notifications are free;
this uses no Budget Actions or paid reports. Alerts can be delayed and are not
a spending cap. See [Budgets pricing](https://aws.amazon.com/aws-cost-management/aws-budgets/pricing/).

The dedicated `aws.billing` provider uses the same profile and account allowlist
but the global billing endpoint in us-east-1; it does not deploy compute there.
Read access to Budgets succeeded. Creation requires billing permissions and is
not verified by a read-only plan or mocked tests. If you change the amount or
recipient, generate a fresh plan before applying. The current local inputs enable
this budget; its recipient is stored in ignored `terraform.tfvars` and the private
saved plan rather than committed examples.

## Plan and approval

Install Terraform >= 1.9, AWS CLI v2, and the local Session Manager plugin. Use
the existing credential chain (`AWS_PROFILE`/SSO/shared configuration); never put
keys in Terraform files, environment examples, or user data. The caller needs
read access to EC2 availability zones and AWS's public AMI SSM parameter for
planning. A later apply needs VPC/EC2/IAM/pass-role permissions and optionally
Budgets permissions; a successful plan does not prove apply authorization.

Plans in this workspace were generated with Terraform **1.17.1**; use that exact
version to inspect them, or regenerate a plan with your installed version.
Failed and previous-account/region plans are archived and must not be applied.
Use only the complete Ohio plan identified in `PLAN_REVIEW.md`, after approval.
Validation used a checksum-verified temporary binary at
`/tmp/platform-terraform-bin/terraform`, without changing your system installation.

From this directory:

```bash
export AWS_PROFILE=platform-lab
aws configure list --profile platform-lab
aws sts get-caller-identity --profile platform-lab
cp -n terraform.tfvars.example terraform.tfvars
# Edit terraform.tfvars: aws_profile="platform-lab", region, NEW allowed_account_ids,
# and optional budget recipient. Verify the new account ID before planning.
terraform fmt -check
terraform init
terraform validate
terraform test
terraform plan -out=review.tfplan
terraform show review.tfplan
# Read-only check of the AWS Compose override, from this directory:
python3 ../../deploy/check-config.py
```

`terraform test` uses mocked AWS and command=plan only, never a live apply.
Provider versions/checksums are recorded in `.terraform.lock.hcl`; commit it.
Default state is local: do not commit `.terraform/`, `*.tfstate*`, `*.tfvars`,
or `*.tfplan`. They are ignored. Securely back up state after any future apply.
Avoid concurrent operators. No backend bucket or lock service is created.

If `platform-lab` is missing, configure it locally using your account's supported
authentication method. For IAM Identity Center, use
`aws configure sso --profile platform-lab`, then `aws sso login --profile platform-lab`. If using IAM access
keys, enter them privately with `aws configure --profile platform-lab`; never
send keys in chat. Confirm STS returns the new account before replacing the
allowlist. Selecting an explicit provider profile and account allowlist prevents
an accidental fallback to the previous default account. SSM commands should also
include `--profile platform-lab` or retain the exported `AWS_PROFILE`.

The local readiness run staged Session Manager plugin 1.2.896.0 under `/tmp`
without changing system packages. To use that temporary copy in this session:

```bash
export PATH="/tmp/platform-session-manager/usr/local/sessionmanagerplugin/bin:$PATH"
session-manager-plugin --version
```

For a permanent installation, follow the [official Ubuntu instructions](https://docs.aws.amazon.com/systems-manager/latest/userguide/install-plugin-debian-and-ubuntu.html).

By default the AMI is discovered from AWS's public AL2023 x86_64 parameter.
Before approving a deployment, put the reviewed `ami_id` in `terraform.tfvars`
and regenerate the plan. Later AMI, user-data, or replacement changes can destroy
the host's data: inspect every plan. Keep the saved plan private. A reviewed Git
commit plus the lock file, pinned AMI, Compose version, and recorded image
versions form the deployment inputs; existing Python base tags remain upstream
maintained, so image rebuilds can pick up base-image updates.

**Only after separate approval**, deploy the reviewed plan:

```bash
terraform apply review.tfplan
terraform output
```

These commands are instructions, not authorization to execute them now. Apply
starts billable resources. This implementation intentionally contains no
`local-exec`, Terraform deployment provisioner, or automated apply step.

## Connect and deploy applications

Terraform user data installs Docker, Git, Python 3, Docker Compose v5.1.1,
and Buildx v0.38.1 (both binaries pinned to official SHA-256 digests), enables Docker/SSM, and bounds
Docker log growth to 3 x 10 MB per container. It contains no secrets. AL2023
provides the SSM agent. Package versions follow AL2023 repositories; retain the
AMI ID and installation logs for troubleshooting. Bootstrap completion is
separate from instance creation; check cloud-init before running deployment.

The operator's IAM identity needs `ssm:StartSession` on this specific instance
and the AWS session documents, plus `ssm:ResumeSession`/`ssm:TerminateSession`
on their own sessions; `ssm:DescribeInstanceInformation` aids discovery. These
operator rights are separate from the restricted instance role. Account-wide
SSM session preferences requiring custom KMS/S3/log permissions are not configured
by this module; adapt them deliberately if your organization requires them.
See [AWS Session Manager access examples](https://docs.aws.amazon.com/systems-manager/latest/userguide/getting-started-restrict-access-quickstart.html).

On your workstation:

```bash
aws ssm describe-instance-information --profile platform-lab --region us-east-2
aws ssm start-session --profile platform-lab --region us-east-2 --target <instance-id>
```

In the SSM shell:

```bash
sudo cloud-init status --wait
sudo tail -n 30 /var/log/cloud-init-output.log
# Publish the reviewed changes to your repository first; select a FULL commit SHA.
sudo git clone https://github.com/andasand/platform-engineering-lab.git /opt/platform-engineering-lab
cd /opt/platform-engineering-lab
sudo git checkout --detach <reviewed-40-character-commit-sha>
sudo bash infra/deploy/deploy.sh
```

The directory created by bootstrap is empty and suitable for `git clone`.
Use your actual public HTTPS repository if its URL changes. Do not put a private
Git token in a URL, shell history, Terraform, or user data. A private repository
requires an independently approved source-transfer process. The current working
tree's uncommitted changes must be reviewed and published before these clone
instructions can deploy them; no commit or push is performed automatically.

`deploy.sh` creates `.env` once with random PostgreSQL and Grafana passwords,
owned by root with mode 0600, then preserves it on every update. Passwords are
never printed or stored in Terraform state. Changing `.env` later does not
rotate an existing database or Grafana administrator password. Protect `.env`
and back it up off-host; root/Docker administrators can read container environment
variables. No paid secrets service is needed for this isolated learning host.

Deployment builds images, starts PostgreSQL, reapplies the additive schema,
starts the applications, and runs the existing smoke and observability checks.
One labeled test row is retained. Docker restart policies bring services back
after a reboot. EventFlow's process-memory events still disappear on restart.
Inspect disk use with `df -h`, `docker system df`, and memory with `free -m`.

### Grafana and application verification

From your workstation, forward remote Grafana to a non-conflicting local port:

```bash
aws ssm start-session --profile platform-lab --region us-east-2 --target <instance-id> \
  --document-name AWS-StartPortForwardingSession \
  --parameters '{"portNumber":["3000"],"localPortNumber":["13000"]}'
```

Open http://localhost:13000/d/platform-lab-observability. Retrieve credentials
privately in the SSM shell with `sudo cat /opt/platform-engineering-lab/.env`;
do not paste the output into tickets or logs. Keep this tunnel open. The local
Grafana on port 3000 is unaffected. Use the same SSM document for Prometheus
(9090 -> 19090), EventFlow (8001 -> 18001), and the ingestor (8002 -> 18002) in
separate sessions if desired. PostgreSQL has no host-published port.

From the instance, with no public port access:

```bash
curl -fsS http://127.0.0.1:8001/health
curl -fsS http://127.0.0.1:8002/health
curl -fsS http://127.0.0.1:8002/ready
curl -fsS http://127.0.0.1:9090/-/ready
curl -fsS http://127.0.0.1:3000/api/health
cd /opt/platform-engineering-lab
sudo python3 scripts/smoke-test.py
sudo python3 scripts/observability-check.py
```

For logs/Compose administration always select both files:

```bash
sudo docker compose -f compose.yaml -f infra/deploy/compose.aws.yaml ps --all
sudo docker compose -f compose.yaml -f infra/deploy/compose.aws.yaml logs --tail 100
```

If SSM is offline, check agent status, instance-role attachment, HTTPS egress,
DNS, internet route, public IPv4, and cloud-init logs using EC2 console output.
Do not open SSH or monitoring ports as a workaround. A role permission or agent
integration problems should be investigated before opening ports. The current
instance has verified SSM connectivity and completed bootstrap; applications
remain undeployed.

## Update applications without changing infrastructure

Take a backup first, then in an SSM shell:

```bash
cd /opt/platform-engineering-lab
sudo bash infra/deploy/backup.sh
sudo git fetch origin
sudo git checkout --detach <new-reviewed-40-character-commit-sha>
sudo bash infra/deploy/deploy.sh
```

This preserves Docker volumes and `.env`, and requires no Terraform apply.
Review database changes before updating. The current initializer only creates
the table if absent; it does not migrate or delete existing tables. Application
rollback can use the prior commit; schema rollback is a separate reviewed task.

## Back up before replacement or destroy

Run `sudo bash infra/deploy/backup.sh` on EC2. It makes a consistent PostgreSQL
custom-format dump and briefly stops Prometheus/Grafana to archive their volume
contents consistently, restarting them on exit. It also saves `.env`, the Git
revision, image tags, Compose version, and SHA-256 checksums. PostgreSQL stays up.
The backup directory is mode 0700, under `backups/<UTC-timestamp>/`, and contains
secrets. **A backup on the root disk alone is lost with the instance.**

To download without provisioning another AWS service, in an SSM shell serve
only this backup directory on loopback:

```bash
sudo python3 -m http.server 8088 --bind 127.0.0.1 \
  --directory /opt/platform-engineering-lab/backups/<UTC-timestamp>
```

In another workstation terminal, forward remote 8088 to local 18088 with
`AWS-StartPortForwardingSession` as above. In a third terminal:

```bash
mkdir -p backups/<UTC-timestamp>
chmod 700 backups/<UTC-timestamp>
cd backups/<UTC-timestamp>
for artifact in postgres.dump environment.env revision.txt compose-version.txt images.txt prometheus.tar.gz grafana.tar.gz SHA256SUMS; do
  curl --fail --output "$artifact" "http://127.0.0.1:18088/$artifact"
done
chmod 600 ./*
sha256sum --check SHA256SUMS
```

Stop the temporary HTTP server and tunnel when done. Store the downloaded
backup in encrypted storage. Restore PostgreSQL using `pg_restore --no-owner
--no-acl --exit-on-error` into a **fresh empty database**, with the saved `.env`.
Restore Grafana/Prometheus archives into their empty volumes while those services
are stopped, retaining ownership. Never restore over existing records or use
`--clean` without separate approval. Test restoration on a separately approved
host before relying on backups; restoration has not been exercised on AWS.

After verifying off-host backups and receiving **separate destructive approval**:

```bash
terraform plan -destroy -out=destroy.tfplan
terraform show destroy.tfplan
terraform destroy
```

These commands have not been run. Destroy terminates EC2 and deletes its root
EBS disk (`delete_on_termination = true`), including PostgreSQL records, Grafana
users/settings, Prometheus history, `.env`, images, and on-host backups. Instance
replacement has the same data-loss risk. Local workstation data is unrelated
and unaffected. Do not run `docker compose down -v`. If you choose to retain disks
or create snapshots instead, document their ongoing charges and cleanup plan.
