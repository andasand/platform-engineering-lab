#!/bin/bash
# Run BEFORE instance replacement or destroy. Does not delete any source records.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"
backup_dir="$deploy_root/backups/$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$backup_dir"
chmod 0700 "$backup_dir"

# Transactionally consistent logical dump, including the security_events schema.
"${compose[@]}" exec -T postgres sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' > "$backup_dir/postgres.dump"
cp --preserve=mode .env "$backup_dir/environment.env"
git rev-parse HEAD > "$backup_dir/revision.txt"
docker compose version > "$backup_dir/compose-version.txt"

# Stop writers for consistent SQLite/TSDB archives. Existing PostgreSQL stays up.
resume_observability() { "${compose[@]}" start prometheus grafana; }
trap resume_observability EXIT
"${compose[@]}" stop prometheus grafana
for service in prometheus grafana; do
  container_id=$("${compose[@]}" ps --all --quiet "$service")
  volume_path=$(docker inspect --format '{{range .Mounts}}{{if eq .Type "volume"}}{{.Source}}{{end}}{{end}}' "$container_id")
  if [[ $volume_path != /var/lib/docker/volumes/*/_data ]]; then
    echo "Unexpected $service volume path; refusing archive." >&2
    exit 1
  fi
  tar -C "$volume_path" -czf "$backup_dir/$service.tar.gz" .
done
"${compose[@]}" config --images > "$backup_dir/images.txt"
(cd "$backup_dir" && sha256sum postgres.dump environment.env revision.txt compose-version.txt images.txt prometheus.tar.gz grafana.tar.gz > SHA256SUMS)
echo "Backup complete: $backup_dir"
echo 'This archive contains secrets. Copy it off the instance securely before destroying infrastructure.'
