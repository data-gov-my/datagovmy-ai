#!/usr/bin/env bash
DATESTAMP="$(date +%FT%H:%m)"

sudo systemctl restart ai-api.service
sudo systemctl restart docs-ast-dagster.service
echo "[${DATESTAMP}] application started"