#!/usr/bin/env bash
DATESTAMP="$(date +%FT%H:%m)"

CD_INSTALL_TARGET=/home/ubuntu/datagovmy-ai
DEPLOY_TEMP=/home/ubuntu/deploy-tmp
DOCS_API_ROOT=${CD_INSTALL_TARGET}/src/docs_assistant
DOCS_API_ENV=${DOCS_API_ROOT}/.env
WEAVIATE_ENV=${DOCS_API_ROOT}/scripts/weaviate/.env
BIN_FILE=${DOCS_API_ROOT}/key.bin

# update permissions
sudo chown -R ubuntu:ubuntu ${CD_INSTALL_TARGET}

# setup python environment if doesn't exist
if [ ! -d "${CD_INSTALL_TARGET}/env" ]; then
    cd $CD_INSTALL_TARGET
    python -m venv $CD_INSTALL_TARGET/env
fi
source env/bin/activate
pip install -r requirements.txt

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

echo "[${DATESTAMP}] post install step completed"
