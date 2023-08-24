#!/usr/bin/env bash
DATESTAMP="$(date +%FT%H:%m)"

CD_INSTALL_TARGET=/home/ubuntu/datagovmy-ai-deploy
DEPLOY_TEMP=/home/ubuntu/deploy-tmp
DOCS_API_ROOT=${CD_INSTALL_TARGET}/src/docs_assistant
DOCS_API_ENV=${DOCS_API_ROOT}/.env
WEAVIATE_ENV=${DOCS_API_ROOT}/scripts/weaviate/.env

# setup python environment
cd $CD_INSTALL_TARGET
python -m venv $CD_INSTALL_TARGET/env
source env/bin/activate
pip install -r requirements.txt

# restore .env files if exists
# if [ -f ${DEPLOY_TEMP}/main.env.bak ]; then
#     echo "[${DATESTAMP}] restoring .env"
#     cp ${DEPLOY_TEMP}/main.env.bak ${DOCS_API_ENV}
# if [ -f ${DEPLOY_TEMP}/weaviate.env.bak ]; then
#     echo "[${DATESTAMP}] restoring weaviate.env"
#     cp ${DEPLOY_TEMP}/weaviate.env.bak ${WEAVIATE_ENV}

echo "[${DATESTAMP}] post install step completed"