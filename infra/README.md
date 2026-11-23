# infra

AWS CDK (Python) for the datagovmy-ai platform: ECS Fargate, PostgreSQL with
pgvector on RDS, a scheduled ingest task, and deploys from GitHub Actions over
OIDC. Stacks are defined in `stacks/`, wired together in `app.py`, and
configured through `cdk.json` context.

## Local commands

```sh
cd infra
uv venv -p 3.12 .venv && uv pip install -p .venv/bin/python -r requirements.txt
. .venv/bin/activate
npx aws-cdk@2.1145.0 synth   # no credentials needed (placeholder account)
```

Deploying needs account-specific values (`CDK_DEPLOY_ACCOUNT`, `EDGE_CONFIG`,
`DATAGPT_UI_IMAGE`) that are not kept in this repository. Secrets live in SSM
Parameter Store and are never committed.

## Scripts

- `scripts/copy_params.sh <env>`: copy SSM parameters between regions.
- `scripts/smoke_test.py <base-url>`: check the serving contract of a deployment.
