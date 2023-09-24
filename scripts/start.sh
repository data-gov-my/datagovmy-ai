#!/usr/bin/env bash
DATESTAMP="$(date +%FT%H:%m)"

sudo systemctl restart ai-docs-api.service
# sudo systemctl restart ai-docs-pipeline.service
echo "[${DATESTAMP}] application started"
