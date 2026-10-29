#!/usr/bin/env bash
# Copy the Singapore prod parameters into ap-southeast-5 as SecureString.
#
#   infra/scripts/copy_params.sh <target-env> [--overwrite]
#   e.g. infra/scripts/copy_params.sh staging
#
# Values never touch the command line, environment or terminal: each one is
# piped from get-parameter straight into put-parameter as JSON on stdin.
# Run with credentials for the prod account (CloudShell works).
set -euo pipefail

TARGET_ENV=${1:?usage: copy_params.sh <staging|prod> [--overwrite]}
OVERWRITE=false
[[ ${2:-} == "--overwrite" ]] && OVERWRITE=true

SRC_REGION=ap-southeast-1
SRC_PREFIX=/datagovmy-ai/prod
DST_REGION=ap-southeast-5
DST_PREFIX=/datagovmy-ai/$TARGET_ENV

# Replaced on the new platform: CHROMA_* and REC_MGR_CONN_STR by
# DATABASE_URL / DATABASE_URL_INGEST (DB bootstrap), KEY_FILE by AUTH_TOKEN.
NAMES=(
    OPENAI_API_KEY LANGCHAIN_API_KEY LANGCHAIN_ENDPOINT LANGCHAIN_PROJECT
    LANGCHAIN_TRACING_V2 MASTER_TOKEN_KEY BACKEND_CORS_ORIGINS DOCS_VINDEX
    DC_META_PARQUET DC_METAFIELDS_PARQUET GITHUB_TOKEN GITHUB_REPO GITHUB_PATH
    DISCORD_WEBHOOK_URL
)

for name in "${NAMES[@]}"; do
    aws ssm get-parameter --region "$SRC_REGION" --name "$SRC_PREFIX/$name" \
        --with-decryption --output json |
        jq --arg n "$DST_PREFIX/$name" --argjson o "$OVERWRITE" \
            '{Name: $n, Value: .Parameter.Value, Type: "SecureString", Overwrite: $o}' |
        aws ssm put-parameter --region "$DST_REGION" \
            --cli-input-json file:///dev/stdin >/dev/null
    echo "copied $name -> $DST_PREFIX/$name"
done

cat <<EOF

Still to create in $DST_REGION (SecureString):
  $DST_PREFIX/AUTH_TOKEN           rolling token. Staging: any test value.
                                   Prod: nothing to do; the first token roll after
                                   cutover step 2 writes it (infra/README.md).
  $DST_PREFIX/DATABASE_URL         read-only app user  (db_bootstrap.py task)
  $DST_PREFIX/DATABASE_URL_INGEST  read-write ingest user (db_bootstrap.py task)
For staging, consider replacing MASTER_TOKEN_KEY with a staging-only value.
EOF
