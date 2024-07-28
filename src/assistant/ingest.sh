#!/bin/bash
echo "Starting ingest script..."
python /app/ingest.py
echo "Ingest script completed. Setting up cron..."
cron -f
echo "Cron setup completed."
