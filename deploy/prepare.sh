#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

read -r -p "Public domain or IPv4 address: " target

if [[ "$target" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]]; then
  IFS='.' read -r first second third fourth <<< "$target"
  for octet in "$first" "$second" "$third" "$fourth"; do
    if (( 10#$octet > 255 )); then
      echo "IPv4 address is invalid." >&2
      exit 1
    fi
  done
  site_address="http://$target"
elif [[ "$target" =~ ^[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?$ ]] && [[ "$target" == *.* ]]; then
  site_address="$target"
else
  echo "Enter a valid public IPv4 address or domain." >&2
  exit 1
fi

umask 077
cat > .env.production <<EOF
TROPOS_SITE_ADDRESS=$site_address
EOF

mkdir -p data
sudo chown 10001:10001 data
docker compose --env-file .env.production config >/dev/null
if [[ "$site_address" == http://* ]]; then
  echo "Production configuration created for temporary HTTP access at $site_address."
  echo "Add a domain later to enable automatic HTTPS."
else
  echo "Production configuration created. Point $site_address to this server before starting Docker Compose."
fi
