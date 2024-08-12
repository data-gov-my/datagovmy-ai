#!/usr/bin/env bash
# this script is run as root
DATESTAMP="$(date +%FT%H:%m)"

CD_INSTALL_TARGET=/home/ubuntu/datagovmy-ai
DOCS_API_ROOT=${CD_INSTALL_TARGET}/src/assistant

# install services
echo "[${DATESTAMP}] installing services"
sudo ln -s ${DOCS_API_ROOT}/config/*.service /etc/systemd/system/
sudo systemctl daemon-reload

echo "[${DATESTAMP}] post install step completed"
