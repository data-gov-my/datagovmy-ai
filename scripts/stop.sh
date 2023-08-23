#!/usr/bin/env bash
DATESTAMP="$(date +%FT%H:%m)"

if [ -f "/etc/systemd/system/ai-api.service" ]
then
  sudo systemctl stop ai-api
  sleep 1
  while [ "$(sudo systemctl is-active ai-api)" == "active" ] 
  do
    sleep 1
  done
  echo "[${DATESTAMP}] application stopped"
fi

if [ -f "/etc/systemd/system/docs-ast-dagster.service" ]
then
  sudo systemctl stop docs-ast-dagster
  sleep 1
  while [ "$(sudo systemctl is-active docs-ast-dagster)" == "active" ] 
  do
    sleep 1
  done
  echo "[${DATESTAMP}] pipeline stopped"
fi