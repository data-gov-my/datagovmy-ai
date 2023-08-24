#!/usr/bin/env bash
DATESTAMP="$(date +%FT%H:%m)"

CD_INSTALL_TARGET=/home/ubuntu/datagovmy-ai-deploy

# update permissions
sudo chown -R ubuntu:ubuntu ${CD_INSTALL_TARGET}

# setup python environment if doesn't exist
if [ ! -d ${CD_INSTALL_TARGET}/env ]; then
    cd $CD_INSTALL_TARGET
    python -m venv $CD_INSTALL_TARGET/env
    source env/bin/activate
    pip install -r requirements.txt
fi

echo "[${DATESTAMP}] post install step completed"
