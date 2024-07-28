#!/usr/bin/env bash
DATESTAMP="$(date +%FT%H:%m)"

set -a
source .env
set +a

docker-compose up -d --build

sudo systemctl restart ai-docs-api.service

echo "[${DATESTAMP}] application started"
