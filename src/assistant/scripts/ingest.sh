#!/bin/bash
echo "Starting ingest script..."
date
source /app/.env
# Never dump the environment here: it holds every secret and this log is kept

# Set timeout to prevent hanging (15 minutes)
timeout 900 /usr/local/bin/python /app/ingest.py
exit_code=$?

if [ $exit_code -eq 124 ]; then
    echo "Ingest script timed out after 15 minutes"
    exit 1
elif [ $exit_code -ne 0 ]; then
    echo "Ingest script failed with exit code: $exit_code"
    exit $exit_code
else
    echo "Ingest script completed successfully"
fi
