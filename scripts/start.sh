#!/usr/bin/env bash
DATESTAMP="$(date +%FT%H:%m)"

sudo systemctl restart ai-api.service
echo "[${DATESTAMP}] application started"