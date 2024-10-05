#!/usr/bin/env bash
# this script is run as user
DATESTAMP="$(date +%FT%H:%m)"

CD_INSTALL_TARGET=/home/ubuntu/datagovmy-ai
DEPLOY_TEMP=/home/ubuntu/deploy-tmp
DOCS_API_ROOT=${CD_INSTALL_TARGET}/src/assistant
DOCS_API_ENV=${CD_INSTALL_TARGET}/.env
REC_MGR_DATA_DIR=/home/ubuntu/datagovmy-ai/data/records

# Ensure the AWS CLI is available
if ! command -v aws &>/dev/null; then
    echo "AWS CLI could not be found. Installing..."
    curl "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o "awscliv2.zip"
    unzip awscliv2.zip
    sudo ./aws/install
    rm -rf aws awscliv2.zip
fi

AWS_REGION="ap-southeast-1"

echo "Fetching environment variables from AWS Parameter Store..."

# Clear existing .env file or create if it doesn't exist
>$DOCS_API_ENV

# List of parameter names to fetch
PARAMS=(
    "/datagovmy-ai/prod/CHROMA_HOST"
    "/datagovmy-ai/prod/CHROMA_PORT"
    "/datagovmy-ai/prod/DC_METAFIELDS_PARQUET"
    "/datagovmy-ai/prod/DC_META_PARQUET"
    "/datagovmy-ai/prod/DOCS_VINDEX"
    "/datagovmy-ai/prod/ENVIRONMENT"
    "/datagovmy-ai/prod/GITHUB_PATH"
    "/datagovmy-ai/prod/GITHUB_REPO"
    "/datagovmy-ai/prod/GITHUB_TOKEN"
    "/datagovmy-ai/prod/KEY_FILE"
    "/datagovmy-ai/prod/LANGCHAIN_API_KEY"
    "/datagovmy-ai/prod/LANGCHAIN_ENDPOINT"
    "/datagovmy-ai/prod/LANGCHAIN_PROJECT"
    "/datagovmy-ai/prod/LANGCHAIN_TRACING_V2"
    "/datagovmy-ai/prod/MASTER_TOKEN_KEY"
    "/datagovmy-ai/prod/OPENAI_API_KEY"
    "/datagovmy-ai/prod/REC_MGR_CONN_STR"
    "/datagovmy-ai/prod/BACKEND_CORS_ORIGINS"
)

for PARAM_NAME in "${PARAMS[@]}"; do
    PARAM_VALUE=$(aws ssm get-parameter --name "$PARAM_NAME" --with-decryption --query "Parameter.Value" --output text --region $AWS_REGION)
    if [ $? -eq 0 ]; then
        # Extract the parameter name without the path
        ENV_VAR_NAME=$(basename $PARAM_NAME)
        echo "$ENV_VAR_NAME=$PARAM_VALUE" >>$DOCS_API_ENV
        echo "Added $ENV_VAR_NAME to .env file"
    else
        echo "Failed to fetch $PARAM_NAME"
    fi
done

echo "Environment setup complete. .env file created at $DOCS_API_ENV"

# Ensure record manager data directory exists
mkdir -p $REC_MGR_DATA_DIR
sudo chown -R ubuntu:ubuntu $REC_MGR_DATA_DIR

# Create record manager cache file if it doesn't exist
if [ ! -f "$REC_MGR_DATA_DIR/record_manager_cache.sql" ]; then
    touch $REC_MGR_DATA_DIR/record_manager_cache.sql
    sudo chown ubuntu:ubuntu $REC_MGR_DATA_DIR/record_manager_cache.sql
fi

echo "[${DATESTAMP}] pre install step completed"
