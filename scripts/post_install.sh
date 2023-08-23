#!/usr/bin/env bash
DATESTAMP="$(date +%FT%H:%m)"

CD_INSTALL_TARGET=/home/ubuntu/datagovmy-ai-deploy

cd ${CD_INSTALL_TARGET}
python -m venv env
source env/bin/activate
pip install -r requirements.txt

echo "[${DATESTAMP}] post install step completed"