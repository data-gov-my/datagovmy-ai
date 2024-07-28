#!/usr/bin/env bash
# this script is run as user
DATESTAMP="$(date +%FT%H:%m)"

CD_INSTALL_TARGET=/home/ubuntu/datagovmy-ai
DEPLOY_TEMP=/home/ubuntu/deploy-tmp
DOCS_API_ROOT=${CD_INSTALL_TARGET}/src/assistant
DOCS_API_ENV=${DOCS_API_ROOT}/.env
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
    "/myapp/prod/DATABASE_URL"
    "/myapp/prod/API_KEY"
    "/myapp/prod/FASTAPI_ENV"
    # Add more parameters as needed
)

for PARAM_NAME in "${PARAMS[@]}"; do
    PARAM_VALUE=$(aws ssm get-parameter --name "$PARAM_NAME" --with-decryption --query "Parameter.Value" --output text --region $AWS_REGION)
    if [ $? -eq 0 ]; then
        # Extract the parameter name without the path
        ENV_VAR_NAME=$(basename $PARAM_NAME)
        echo "$ENV_VAR_NAME=$PARAM_VALUE" >>$ENV_FILE
        echo "Added $ENV_VAR_NAME to .env file"
    else
        echo "Failed to fetch $PARAM_NAME"
    fi
done

echo "Environment setup complete. .env file created at $DOCS_API_ENV"

# Ensure record manager data directory exists
sudo mkdir -p $REC_MGR_DATA_DIR
sudo chown -R 1000:1000 $REC_MGR_DATA_DIR
if [ ! -f $DOCS_API_ENV ]; then
    touch $REC_MGR_DATA_DIR/record_manager_cache.sql
fi

echo "[${DATESTAMP}] pre install step completed"
