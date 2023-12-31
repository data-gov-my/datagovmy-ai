#!/usr/bin/env bash
# this script is run as user
DATESTAMP="$(date +%FT%H:%m)"

CD_INSTALL_TARGET=/home/ubuntu/datagovmy-ai
DEPLOY_TEMP=/home/ubuntu/deploy-tmp
DOCS_API_ROOT=${CD_INSTALL_TARGET}/src/assistant
DOCS_API_ENV=${DOCS_API_ROOT}/.env
WEAVIATE_ENV=${DOCS_API_ROOT}/scripts/weaviate/.env
BIN_FILE=${DOCS_API_ROOT}/key.bin

# preserve .env files from previous deployment
if [ -f ${DOCS_API_ENV} ]; then
    echo "[${DATESTAMP}] preserving .env file"
    cp ${DOCS_API_ENV} ${DEPLOY_TEMP}/main.env.bak
fi
if [ -f ${WEAVIATE_ENV} ]; then
    echo "[${DATESTAMP}] preserving weaviate .env file"
    cp ${WEAVIATE_ENV} ${DEPLOY_TEMP}/weaviate.env.bak
fi
if [ -f ${BIN_FILE} ]; then
    echo "[${DATESTAMP}] preserving key file"
    cp ${BIN_FILE} ${DEPLOY_TEMP}/key.bin
fi

# clear previous deployment if exists
if [ -d ${CD_INSTALL_TARGET} ]; then
    echo "[${DATESTAMP}] clearing previous deployment"
    rm -rf ${CD_INSTALL_TARGET}
    mkdir ${CD_INSTALL_TARGET}
fi

echo "[${DATESTAMP}] pre install step completed"
