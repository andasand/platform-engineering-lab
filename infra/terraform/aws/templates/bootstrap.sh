#!/bin/bash
set -euo pipefail
umask 027

# AL2023 includes the SSM agent. No AWS credentials or application secrets here.
dnf install -y docker git python3
systemctl enable --now docker amazon-ssm-agent

install -d -m 0755 /usr/local/lib/docker/cli-plugins
compose_version=v5.1.1
compose_asset=docker-compose-linux-x86_64
work_dir=$(mktemp -d)
trap 'rm -rf "$work_dir"' EXIT
curl --fail --location --retry 5 "https://github.com/docker/compose/releases/download/$compose_version/$compose_asset" -o "$work_dir/$compose_asset"
echo "2ac954c9d506b912a12477d72f01601dc72ec918c429c7bae48fd707bdf0f3e5 *$compose_asset" > "$work_dir/checksums"
(cd "$work_dir" && sha256sum --check checksums)
install -m 0755 "$work_dir/$compose_asset" /usr/local/lib/docker/cli-plugins/docker-compose

buildx_version=v0.38.1
buildx_asset="buildx-$buildx_version.linux-amd64"
curl --fail --location --retry 5 "https://github.com/docker/buildx/releases/download/$buildx_version/$buildx_asset" -o "$work_dir/$buildx_asset"
echo "e25d5cd675092bb5d1d03632fdc46cdf3fede7b514dc5f023a105e00d3ce5ad4 *$buildx_asset" > "$work_dir/buildx-checksums"
(cd "$work_dir" && sha256sum --check buildx-checksums)
install -m 0755 "$work_dir/$buildx_asset" /usr/local/lib/docker/cli-plugins/docker-buildx

# Bound log growth on this small disk. This is a fresh dedicated EC2 host.
install -d -m 0755 /etc/docker
cat > /etc/docker/daemon.json <<'JSON'
{"log-driver":"json-file","log-opts":{"max-size":"10m","max-file":"3"}}
JSON
systemctl restart docker
install -d -m 0750 /opt/platform-engineering-lab
touch /etc/platform-engineering-lab-aws
chmod 0600 /etc/platform-engineering-lab-aws
docker compose version
docker buildx version
echo 'Platform lab bootstrap complete; deploy a reviewed repository commit through SSM.'
