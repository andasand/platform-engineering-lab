#!/bin/bash
# Re-run after checking out a reviewed commit; volumes and existing secrets persist.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

python3 - <<'PY'
import os
from pathlib import Path
import secrets

path = Path('.env')
if not path.exists():
    values = {
        'POSTGRES_HOST': 'data-engineering-postgres',
        'POSTGRES_PORT': '5432',
        'POSTGRES_DB': 'data_engineering',
        'POSTGRES_USER': 'pipeline_user',
        'POSTGRES_PASSWORD': secrets.token_urlsafe(32),
        'POSTGRES_NETWORK': 'platform-engineering-lab_database',
        'EVENTFLOW_PORT': '8001',
        'LOG_INGESTOR_PORT': '8002',
        'GF_SECURITY_ADMIN_USER': 'admin',
        'GF_SECURITY_ADMIN_PASSWORD': secrets.token_urlsafe(32),
    }
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as output:
        output.write(''.join(f'{key}={value}\n' for key, value in values.items()))
    print('Generated new host-only application secrets; nothing written to Terraform state.')
else:
    path.chmod(0o600)
    print('Existing .env preserved; no password rotation.')
PY

"${compose[@]}" config --quiet
"${compose[@]}" build eventflow
"${compose[@]}" build log-ingestor
# Repeat additive initialization on updates too; do not depend on a prior exited job.
"${compose[@]}" up -d --wait --wait-timeout 120 postgres
"${compose[@]}" run --rm --no-deps db-init
"${compose[@]}" up -d --wait --wait-timeout 240
"${compose[@]}" ps --all
python3 scripts/smoke-test.py
# The existing checker resolves root compose.yaml; endpoints/credentials are identical.
python3 scripts/observability-check.py
