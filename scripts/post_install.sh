#!/usr/bin/env bash
# this script is run as root
DATESTAMP="$(date +%FT%H:%m)"

CD_INSTALL_TARGET=/home/ubuntu/datagovmy-ai
DEPLOY_TEMP=/home/ubuntu/deploy-tmp
DOCS_API_ROOT=${CD_INSTALL_TARGET}/src/assistant
DOCS_API_ENV=${DOCS_API_ROOT}/.env

# install services
# echo "[${DATESTAMP}] installing services"
# ln -s ${DOCS_API_ROOT}/config/*.service /etc/systemd/system/
# systemctl daemon-reload

echo "[${DATESTAMP}] post install step completed"
