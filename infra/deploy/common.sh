#!/bin/bash
# Source this file; refuse to mutate a developer machine or an unrelated host.
set -euo pipefail
if [[ $EUID -ne 0 || ! -f /etc/platform-engineering-lab-aws ]]; then
  echo 'Run with sudo on the Terraform-bootstrapped AWS host only.' >&2
  exit 1
fi
deploy_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
if [[ $deploy_root != /opt/platform-engineering-lab ]]; then
  echo 'Expected checkout at /opt/platform-engineering-lab; refusing another directory.' >&2
  exit 1
fi
cd "$deploy_root"
umask 077
compose=(docker compose --project-directory "$deploy_root" --env-file "$deploy_root/.env" -f "$deploy_root/compose.yaml" -f "$deploy_root/infra/deploy/compose.aws.yaml")
