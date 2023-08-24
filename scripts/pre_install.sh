#!/usr/bin/env bash
DATESTAMP="$(date +%FT%H:%m)"
CD_INSTALL_TARGET=/home/ubuntu/datagovmy-ai-deploy
DEPLOY_TEMP=/home/ubuntu/deploy-tmp
DOCS_API_ROOT=${CD_INSTALL_TARGET}/src/docs_assistant
DOCS_API_ENV=${DOCS_API_ROOT}/.env
WEAVIATE_ENV=${DOCS_API_ROOT}/scripts/weaviate/.env

# preserve .env files from previous deployment
# if [ -f ${DOCS_API_ENV} ]; then
#     echo "[${DATESTAMP}] preserving .env file"
#     cp ${DOCS_API_ENV} ${DEPLOY_TEMP}/main.env.bak
# if [ -f ${WEAVIATE_ENV} ]; then
#     echo "[${DATESTAMP}] preserving weaviate .env file"
#     cp ${WEAVIATE_ENV} ${DEPLOY_TEMP}/weaviate.env.bak

echo "[${DATESTAMP}] pre install step completed"