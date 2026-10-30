#!/usr/bin/env bash
# Copy the Singapore prod parameters into ap-southeast-5 as SecureString.
#
#   infra/scripts/copy_params.sh <target-env> [--overwrite]
#   e.g. infra/scripts/copy_params.sh staging
#
# Values never reach the command line, environment or terminal: each one goes
# from get-parameter into a private temp file (0600, removed on exit) and from
# there into put-parameter. Run with credentials for the prod account
# (CloudShell works).
set -euo pipefail

TARGET_ENV=${1:?usage: copy_params.sh <staging|prod> [--overwrite]}
OVERWRITE=false
[[ ${2:-} == "--overwrite" ]] && OVERWRITE=true
command -v jq >/dev/null || { echo "jq is required" >&2; exit 1; }

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

tmp=$(mktemp)
trap 'rm -f "$tmp"' EXIT

copied=0 skipped=0
for name in "${NAMES[@]}"; do
    dst="$DST_PREFIX/$name"
    existing=$(aws ssm describe-parameters --region "$DST_REGION" \
        --parameter-filters "Key=Name,Values=$dst" \
        --query 'Parameters[0].Type' --output text)
    if [[ $existing == "String" || $existing == "StringList" ]]; then
        # Parameter Store can't change String to SecureString
        echo "SKIP $name: $dst exists as plain $existing; delete it first"
        skipped=$((skipped + 1))
        continue
    fi
    if [[ $existing == "SecureString" && $OVERWRITE == false ]]; then
        echo "SKIP $name: $dst exists (use --overwrite to replace)"
        skipped=$((skipped + 1))
        continue
    fi

    aws ssm get-parameter --region "$SRC_REGION" --name "$SRC_PREFIX/$name" \
        --with-decryption --output json |
        jq --arg n "$dst" --argjson o "$OVERWRITE" \
            '{Name: $n, Value: .Parameter.Value, Type: "SecureString", Overwrite: $o}' \
            >"$tmp"
    aws ssm put-parameter --region "$DST_REGION" \
        --cli-input-json "file://$tmp" >/dev/null
    : >"$tmp"
    echo "copied $name -> $dst"
    copied=$((copied + 1))
done

cat <<EOF

$copied copied, $skipped skipped.
Still to create in $DST_REGION (SecureString):
  $DST_PREFIX/AUTH_TOKEN           rolling token. Staging: any test value.
                                   Prod: nothing to do; the first token roll after
                                   cutover step 2 writes it (infra/README.md).
  $DST_PREFIX/DATABASE_URL         read-only app user  (db_bootstrap.py task)
  $DST_PREFIX/DATABASE_URL_INGEST  read-write ingest user (db_bootstrap.py task)
For staging, consider replacing MASTER_TOKEN_KEY with a staging-only value.
EOF
