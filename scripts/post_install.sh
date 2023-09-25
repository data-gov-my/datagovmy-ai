#!/usr/bin/env bash
# this script is run as root
DATESTAMP="$(date +%FT%H:%m)"

CD_INSTALL_TARGET=/home/ubuntu/datagovmy-ai
DEPLOY_TEMP=/home/ubuntu/deploy-tmp
DOCS_API_ROOT=${CD_INSTALL_TARGET}/src/assistant
DOCS_API_ENV=${DOCS_API_ROOT}/.env
WEAVIATE_ENV=${DOCS_API_ROOT}/scripts/weaviate/.env
BIN_FILE=${DOCS_API_ROOT}/key.bin

cd $CD_INSTALL_TARGET
# setup python environment if doesn't exist
if [ ! -d "${CD_INSTALL_TARGET}/env" ]; then
    /home/ubuntu/.pyenv/shims/python -m venv $CD_INSTALL_TARGET/env
fi
source env/bin/activate
python -m pip install -r requirements/main.txt

# TODO: copy weaviate out
# cp ${DOCS_API_ROOT}/scripts/weaviate

# restore files if exists
if [ -f ${DEPLOY_TEMP}/main.env.bak ]; then
    echo "[${DATESTAMP}] restoring .env"
    cp ${DEPLOY_TEMP}/main.env.bak ${DOCS_API_ENV}
fi
if [ -f ${DEPLOY_TEMP}/weaviate.env.bak ]; then
    echo "[${DATESTAMP}] restoring weaviate.env"
    cp ${DEPLOY_TEMP}/weaviate.env.bak ${WEAVIATE_ENV}
fi
if [ -f ${DEPLOY_TEMP}/key.bin ]; then
    echo "[${DATESTAMP}] restoring key file"
    cp ${DEPLOY_TEMP}/key.bin ${BIN_FILE}
fi

# update permissions
sudo chown -R ubuntu:ubuntu ${CD_INSTALL_TARGET}

# install services
echo "[${DATESTAMP}] installing services"
ln -s ${DOCS_API_ROOT}/config/*.service /etc/systemd/system/
systemctl daemon-reload

# install ingest crontab
cron_job="0 * * * * ${CD_INSTALL_TARGET}/env/bin/python ${CD_INSTALL_TARGET}/src/assistant/ingest.py"
if ! (sudo -u ubuntu crontab -l | grep -q "$cron_job"); then
    (sudo -u ubuntu crontab -l ; echo "$cron_job") | sudo -u ubuntu crontab -
fi

echo "[${DATESTAMP}] post install step completed"
