#!/usr/bin/env bash
DATESTAMP="$(date +%FT%H:%m)"

if [ -f "/etc/systemd/system/ai-docs-api.service" ]; then
  sudo systemctl stop ai-docs-api
  sleep 1
  while [ "$(sudo systemctl is-active ai-docs-api)" == "active" ]; do
    sleep 1
  done
  echo "[${DATESTAMP}] application stopped"
fi
