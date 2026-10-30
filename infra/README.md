# infra

AWS CDK (Python) for the datagovmy-ai platform in `ap-southeast-5` (Malaysia):
ECS Fargate, PostgreSQL + pgvector on RDS, ingest as a scheduled task, deploys
from GitHub Actions over OIDC.

This builds a new stack **alongside** the Singapore one. Clients keep using
`https://ai.datagovmy.app`, which points at the existing Singapore ALB; that
ALB forwards to Malaysia over VPC peering, and the cutover is a change to its
listener rules, not to DNS. Hostnames, ports, paths, certificates and
request/response shapes stay the same, so the frontend and the token-roll
Lambda need no change.

```
client ─► ai.datagovmy.app (CNAME unchanged) ─► Singapore ALB ─┬─► old EC2 host (legacy target groups)
                                                               └─► VPC peering ─► Malaysia internal NLB ─► ECS tasks
```

## Stacks

| Stack | Region | Contents |
| --- | --- | --- |
| `DatagovmyAi-Network` | MY | VPC (2 AZs, public / app / db subnets, 1 NAT), peering to the Singapore VPC, security groups |
| `DatagovmyAi-Data` | MY | RDS PostgreSQL 17 (pgvector), admin password in Secrets Manager |
| `DatagovmyAi-Platform` | MY | ECR repo, ECS cluster, internal NLB (fixed IPs `10.40.4.10`, `10.40.8.10`), alerts → Discord; with `publicAlb`, its own ALB + WAF |
| `DatagovmyAi-Service-staging` | MY | API service (NLB :8001), ingest task + schedule, DB bootstrap task |
| `DatagovmyAi-Service-prod` | MY | API service (NLB :8000), ingest task + schedule, DB bootstrap task, alarms, `datagpt-ui` (NLB :3001) |
| `DatagovmyAi-Edge` | SG | On the existing ALB: route to Malaysia, target groups for the NLB IPs, listener rules, staging listener `:8443`, WAF |
| `DatagovmyAi-Ci` | MY | GitHub OIDC provider and the `staging`, `prod` and `ingest` roles |

`DatagovmyAi-Edge` only adds to the console-built Singapore ALB; it never
changes the listeners' default actions. Deleting it puts the ALB back as it
was.

## Local commands

```sh
cd infra
uv venv -p 3.12 .venv && uv pip install -p .venv/bin/python -r requirements.txt
. .venv/bin/activate
npx aws-cdk@2.1145.0 synth          # no credentials needed (placeholder account)
```

Deploying needs `CDK_DEPLOY_ACCOUNT` and `EDGE_CONFIG` set (synth refuses
otherwise), and `DATAGPT_UI_IMAGE` set to the same value CI uses.

`EDGE_CONFIG` is JSON describing the existing Singapore resources:

```json
{
  "vpcId": "vpc-…", "vpcCidr": "172.31.0.0/16", "routeTableIds": ["rtb-…"],
  "albArn": "arn:aws:elasticloadbalancing:ap-southeast-1:…:loadbalancer/app/<name>/…",
  "albSecurityGroupId": "sg-…",
  "certificateArn": "arn:aws:acm:ap-southeast-1:…",
  "listeners": {"443": "arn:…", "8005": "arn:…", "3001": "arn:…"},
  "legacyTargetGroups": {"443": "arn:…", "8005": "arn:…", "3001": "arn:…"},
  "legacyAuthTokenUrl": "http://<old host private IP>:8005/auth-token"
}
```

## First-time setup (admin, once)

1. **Bootstrap CDK** in both regions, with admin credentials for the prod account:
   `npx aws-cdk@2.1145.0 bootstrap aws://<account>/ap-southeast-5 aws://<account>/ap-southeast-1`
2. **Copy parameters** from Singapore (values never printed):
   `infra/scripts/copy_params.sh staging` and `infra/scripts/copy_params.sh prod`.
   For staging, also create `/datagovmy-ai/staging/AUTH_TOKEN` (any test value). Prod's is written by the first token roll after cutover step 2.
3. **Deploy everything**: `CDK_DEPLOY_ACCOUNT=<account> EDGE_CONFIG=… npx aws-cdk@2.1145.0 deploy --all`.
   Services start with 0 tasks and schedules disabled; the Singapore ALB still sends everything to the old host, and its WAF only counts. If the account already has a GitHub OIDC provider, pass `-c createGithubOidcProvider=false`.
4. **GitHub** (repo admin):
   - Environments `staging`, `production` and `ingest`, each with deployment branches limited to `main`. Add required reviewers to `production`.
   - In each environment, `AWS_ROLE_ARN` from the `DatagovmyAi-Ci` outputs (`ingest` uses the ingest role). `BASE_URL`: `https://ai.datagovmy.app:8443` in `staging`, `https://ai.datagovmy.app:8005` in `production`. Secret `SMOKE_CHAT_TOKEN` in `staging` (the staging `AUTH_TOKEN` value).
   - Repository variables `AWS_ACCOUNT_ID`, `EDGE_CONFIG`, `DATAGPT_UI_IMAGE`, and `PLATFORM_DEPLOY_ENABLED=true` when ready.
   - Settings → Actions → require approval for fork pull request workflows from all outside contributors.
5. **Push an image** (run `deploy.yml`, or build `src/assistant/Dockerfile.ecs` for `linux/arm64` and push it to the ECR repo), then deploy the service stacks with that tag.
6. **Bootstrap the databases**, once per environment. The task creates the database, a read-only API user and an ingest user, and writes `DATABASE_URL` / `DATABASE_URL_INGEST` to SSM; nobody sees the passwords. Re-running it is safe (`ROTATE=true` forces new passwords).
   ```sh
   env=staging  # then prod
   out() { aws cloudformation describe-stacks --region ap-southeast-5 --stack-name DatagovmyAi-Service-$env \
     --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text; }
   aws ecs run-task --region ap-southeast-5 --cluster datagovmy-ai --launch-type FARGATE \
     --task-definition "$(out DbBootstrapTaskFamily)" \
     --network-configuration "awsvpcConfiguration={subnets=[$(out AppSubnets)],securityGroups=[$(out AppSecurityGroup)],assignPublicIp=DISABLED}"
   ```
   Then run the ingest task once (`ingest.yml`, or the same command with `IngestTaskFamily`) to build the index.
7. **Turn on services** by raising `apiDesiredCount` and enabling the ingest schedule in `cdk.json`.

After step 4, CI owns deploys: `deploy.yml` for images, `infra.yml` for infra changes (including the cutover).

## Cutover

What clients use:

- The frontend (`datagovmy-front`) calls the API **from the browser** at `NEXT_PUBLIC_AI_URL` = `https://ai.datagovmy.app:8005`: `/chat/stream` and `/generate-meta`, with the rolling token (from Vercel Edge Config, via a cookie) as a Bearer header. CORS matters: `BACKEND_CORS_ORIGINS` must match exactly.
- The token-roll Lambda (`datagovmy-front/lambda/roll_auth_token`) writes the new token to Edge Config, then POSTs it to the backend's `/auth-token` with `Bearer <WORKFLOW_TOKEN>`, which must equal `MASTER_TOKEN_KEY`.

Each step is a pull request changing `cdk.json`, deployed by `infra.yml` after approval:

1. **Test** the new prod stack through the real hostname, with no users on it: requests with the header `x-datagovmy-backend: ecs` go to Malaysia at any weight. `deploy.yml` does this after each prod deploy; for a full check run `SMOKE_HEADER="x-datagovmy-backend: ecs" SMOKE_CHAT_TOKEN=<token> python infra/scripts/smoke_test.py https://ai.datagovmy.app:8005`.
2. **Move token rolls**: `"edgeAuthTokenToNew": true`. `/auth-token` then goes to the new stack, which stores the token and forwards it to the old host (`legacyAuthTokenUrl`), so both sides accept every new token. Wait for one token roll and check that both sides accept it. Synth refuses any weight above 0 before this step.
3. **Shift users**: `"edgeWeights": {"api": 10, "ui": 0}`, then 50, then 100. Watch the Singapore ALB's 5xx and target health, the new stack's alarms, `/chat` latency and 401s.
4. **Rollback** at any step: set the weight back to 0 (seconds, no DNS cache). Token rolls keep reaching the old host through the forward. To stop routing them through the new stack, set `edgeAuthTokenToNew` back to false.
5. Keep the old host for a week at 100%, then decommission it. Until then, its security group must keep allowing port 8005 from `10.40.0.0/16` (the forward).

`datagpt-ui` follows the same pattern with `edgeWeights.ui` once `DATAGPT_UI_IMAGE` is set.

## Moving the DNS later

To retire the Singapore ALB: request an ACM certificate for `ai.datagovmy.app` in `ap-southeast-5` (DNS validation, in DigitalOcean), set `CERTIFICATE_ARN`, deploy with `"publicAlb": true` (Malaysia gets its own ALB and WAF, alongside the NLB), lower the record's TTL from 12 h, then change the `ai.datagovmy.app` CNAME to the new ALB. Once traffic has drained from Singapore, delete `DatagovmyAi-Edge` and the peering.

## Scripts

- `scripts/copy_params.sh <env> [--overwrite]`: Singapore → Malaysia parameters, as SecureString.
- `scripts/smoke_test.py <base-url>`: the serving contract the frontend and the token-roll Lambda rely on. No model calls unless `SMOKE_CHAT_TOKEN` is set; `SMOKE_HEADER` adds a header (e.g. the test header above).
