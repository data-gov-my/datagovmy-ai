# infra

AWS CDK (Python) for the datagovmy-ai platform in `ap-southeast-5` (Malaysia):
ECS Fargate behind an ALB, PostgreSQL + pgvector on RDS, ingest as a scheduled
task, deploys from GitHub Actions over OIDC.

This builds a new stack **alongside** the Singapore one. Nothing here touches
the existing EC2 host, ALB, CodeDeploy group or `workflow.yml`. When it's
ready, the existing serving domains are pointed at the new ALB (see Cutover);
hostnames, ports, paths and request/response shapes stay the same, so the
frontend and the token-roll Lambda need no change.

## Stacks

| Stack | Contents |
| --- | --- |
| `DatagovmyAi-Network` | VPC (2 AZs, public / app / db subnets, 1 NAT), security groups |
| `DatagovmyAi-Data` | RDS PostgreSQL 17 (pgvector), admin password in Secrets Manager |
| `DatagovmyAi-Platform` | ECR repo, ECS cluster, ALB + listeners, WAF, alerts topic → Discord |
| `DatagovmyAi-Service-staging` | API service, ingest task + schedule, DB bootstrap task |
| `DatagovmyAi-Service-prod` | API service, ingest task + schedule, DB bootstrap task, alarms, `datagpt-ui` |
| `DatagovmyAi-Ci` | GitHub OIDC provider and the `staging`, `prod` and `ingest` roles |

ALB listeners: before a certificate is configured, prod is on `:80` and
staging on `:8080`. With `CERTIFICATE_ARN` set: prod on `:443` and `:8005`
(as in Singapore), `:80` redirects, staging on `:8443`. `datagpt-ui` is on
`:3001` either way.

## Local commands

```sh
cd infra
uv venv -p 3.12 .venv && uv pip install -p .venv/bin/python -r requirements.txt
. .venv/bin/activate
npx aws-cdk@2.1145.0 synth          # no credentials needed
```

Deploying needs `CDK_DEPLOY_ACCOUNT` set. The CLI then refuses credentials
for any other account. Also set `CERTIFICATE_ARN` and `DATAGPT_UI_IMAGE` to
the same values CI uses, because listeners and services depend on them.

## First-time setup (admin, once)

1. **Bootstrap CDK** in the region, with admin credentials for the prod account:
   `npx aws-cdk@2.1145.0 bootstrap aws://<account>/ap-southeast-5`
2. **Copy parameters** from Singapore (values never printed):
   `infra/scripts/copy_params.sh staging` and `infra/scripts/copy_params.sh prod`.
   Then create the parameters it lists (`AUTH_TOKEN`; `DATABASE_URL*` come with the DB bootstrap).
3. **Deploy everything** (services start with 0 tasks and schedules disabled):
   `CDK_DEPLOY_ACCOUNT=<account> npx aws-cdk@2.1145.0 deploy --all`
   If the account already has a GitHub OIDC provider, pass `-c createGithubOidcProvider=false`.
4. **GitHub** (repo admin):
   - Environments `staging`, `production` and `ingest`, each with deployment branches limited to `main`. Add required reviewers to `production`.
   - Environment variable `AWS_ROLE_ARN` in each, from the `DatagovmyAi-Ci` outputs (`ingest` uses the ingest role). `BASE_URL` in `staging` and `production`. Secret `SMOKE_CHAT_TOKEN` in `staging`.
   - Repository variables `AWS_ACCOUNT_ID`, `CERTIFICATE_ARN` (empty until the certificate exists), `DATAGPT_UI_IMAGE`, and `PLATFORM_DEPLOY_ENABLED=true` when ready.
   - Settings → Actions → require approval for fork pull request workflows from all outside contributors.
5. **Push an image** (run `deploy.yml`, or build `src/assistant/Dockerfile.ecs` for `linux/arm64` and push it to the ECR repo), then deploy the service stacks with that tag.
6. **Bootstrap the databases**, once per environment. The task creates the database, a read-only API user and an ingest user, and writes `DATABASE_URL` / `DATABASE_URL_INGEST` to SSM; nobody sees the passwords. Re-running it is safe (`ROTATE=true` forces new passwords).
   ```sh
   env=staging  # then prod
   out() { aws cloudformation describe-stacks --stack-name DatagovmyAi-Service-$env \
     --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text; }
   aws ecs run-task --cluster datagovmy-ai --launch-type FARGATE \
     --task-definition "$(out DbBootstrapTaskFamily)" \
     --network-configuration "awsvpcConfiguration={subnets=[$(out AppSubnets)],securityGroups=[$(out AppSecurityGroup)],assignPublicIp=DISABLED}"
   ```
   Then run the ingest task once (`ingest.yml`, or the same command with `IngestTaskFamily`) to build the index.
7. **Turn on services** by raising `apiDesiredCount` and enabling the ingest schedule in `cdk.json`.

After step 4, CI owns deploys: `deploy.yml` for images, `infra.yml` for infra changes.

## Cutover

What clients use today:

- The frontend (`datagovmy-front`) calls the API **from the browser** at `NEXT_PUBLIC_AI_URL` = `https://ai.datagovmy.app:8005`: `/chat/stream` and `/generate-meta`, with the rolling token (from Vercel Edge Config, via a cookie) as a Bearer header. CORS therefore matters: `BACKEND_CORS_ORIGINS` must match exactly.
- The token-roll Lambda (`datagovmy-front/lambda/roll_auth_token`) writes the new token to Edge Config, then POSTs it to the backend's `/auth-token` with `Bearer <WORKFLOW_TOKEN>`, which must equal `MASTER_TOKEN_KEY`.
- `ai.datagovmy.app` is a CNAME to the Singapore ALB, in **DigitalOcean DNS**, TTL 43200 s (12 h). Its certificate is ACM-issued for `ai.datagovmy.app`.

Before cutover: request an ACM certificate for `ai.datagovmy.app` in `ap-southeast-5` (DNS validation; add the CNAME it asks for in DigitalOcean if it isn't there already) and set `CERTIFICATE_ARN`.

1. At least a day before: the new prod stack is healthy on the ALB DNS name, and the TTL on `ai.datagovmy.app` is lowered from 12 h to 60 s (the old TTL must expire first).
2. Copy the parameters into `prod` if not done (`copy_params.sh prod --overwrite` picks up any changes).
3. Right after a scheduled token roll, copy the live token from the Singapore host's `KEY_FILE` into `/datagovmy-ai/prod/AUTH_TOKEN` (an operator does this; the value is never pasted anywhere).
4. Smoke test the new stack with the real token (`curl --resolve ai.datagovmy.app:8005:<new ALB IP>` or the ALB DNS name with `-k`), then change the `ai.datagovmy.app` CNAME to the new ALB's DNS name.
5. Watch ALB 5xx, target health, `/chat` latency, 401s, and the next `/auth-token` call landing on the new stack.
6. Rollback: point the records back at the Singapore ALB. If a token roll has already reached the new stack, copy that token back into the Singapore `KEY_FILE`.
7. Keep the Singapore stack for a week, then decommission it.

## Scripts

- `scripts/copy_params.sh <env> [--overwrite]`: Singapore → Malaysia parameters, as SecureString.
- `scripts/smoke_test.py <base-url>`: the serving contract the frontend and the token-roll Lambda rely on. No model calls unless `SMOKE_CHAT_TOKEN` is set.
