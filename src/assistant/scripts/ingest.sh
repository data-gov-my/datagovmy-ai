#!/bin/bash
echo "Starting ingest script..."
date
source /app/.env
printenv
/usr/local/bin/python /app/ingest.py
echo "Ingest script completed."