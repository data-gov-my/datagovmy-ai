#!/bin/bash
echo "Starting ingest script..."
date
source /app/.env
printenv
python /app/ingest.py
echo "Ingest script completed."
