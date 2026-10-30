#!/usr/bin/env bash
# Synthetic tests only: never load .env or use the normal Compose project.
set -euo pipefail
cd "$(dirname "$0")/.."
test_python="${PHASE3_TEST_PYTHON:-.venv/bin/python}"
test_container="platform-phase3-test-$$"
cleanup() {
    docker rm -f "$test_container" >/dev/null 2>&1 || true
}
# A collision fails before cleanup is registered; never remove another container.
docker run -d --name "$test_container" --label platform-lab.purpose=phase3-synthetic-tests \
    --tmpfs /var/lib/postgresql/data:rw \
    -e POSTGRES_HOST_AUTH_METHOD=trust -e POSTGRES_DB=phase3_synthetic_test \
    -p 127.0.0.1::5432 postgres:16 >/dev/null
trap cleanup EXIT
test_ready=false
for attempt in {1..30}; do
    if docker exec "$test_container" pg_isready -h 127.0.0.1 -U postgres -d phase3_synthetic_test >/dev/null 2>&1; then
        test_ready=true
        break
    fi
    sleep 1
done
"$test_ready" || { echo 'Disposable PostgreSQL did not become ready' >&2; exit 1; }
test_address="$(docker port "$test_container" 5432/tcp)"
[[ "$test_address" == 127.0.0.1:* ]] || exit 1
export PHASE3_TEST_DSN="host=127.0.0.1 port=${test_address##*:} dbname=phase3_synthetic_test user=postgres"
"$test_python" -m unittest collectors.windows_security.test_collector -v
"$test_python" -m unittest discover -s tests -p test_ingestor_idempotency.py -v
