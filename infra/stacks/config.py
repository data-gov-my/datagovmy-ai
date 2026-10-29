"""Settings shared by all stacks.

Values that differ per deployment come from cdk.json context so they can be
overridden with `-c key=value`. Nothing secret lives here: secrets are SSM
SecureString parameters created outside CDK (see scripts/copy_params.sh).
"""

import json
import os
from dataclasses import dataclass

from constructs import Construct

PROJECT = "datagovmy-ai"
REGION = "ap-southeast-5"

# CDK bootstrap qualifier (default); used to scope CI roles to the bootstrap roles
CDK_QUALIFIER = "hnb659fds"

# SSM SecureString parameters, read from /datagovmy-ai/<env>/<NAME>.
# DATABASE_URL* are created by the DB bootstrap (phase 2), the rest are copied
# from Singapore by scripts/copy_params.sh.
API_SECRETS = [
    "OPENAI_API_KEY",
    "LANGCHAIN_API_KEY",
    "LANGCHAIN_ENDPOINT",
    "LANGCHAIN_PROJECT",
    "LANGCHAIN_TRACING_V2",
    "MASTER_TOKEN_KEY",
    "BACKEND_CORS_ORIGINS",
    "DOCS_VINDEX",
    "DC_META_PARQUET",
    "DC_METAFIELDS_PARQUET",
    "DATABASE_URL",
]
INGEST_SECRETS = [
    "OPENAI_API_KEY",
    "LANGCHAIN_API_KEY",
    "LANGCHAIN_ENDPOINT",
    "LANGCHAIN_PROJECT",
    "LANGCHAIN_TRACING_V2",
    "GITHUB_TOKEN",
    "GITHUB_REPO",
    "GITHUB_PATH",
    "DOCS_VINDEX",
    "DC_META_PARQUET",
    "DC_METAFIELDS_PARQUET",
    "DATABASE_URL_INGEST",
]
# Rolling auth token written by /auth-token and read by every API task
AUTH_TOKEN_PARAM = "AUTH_TOKEN"
DISCORD_WEBHOOK_PARAM = f"/{PROJECT}/prod/DISCORD_WEBHOOK_URL"

API_PORT = 8000


def param_name(env: str, name: str) -> str:
    return f"/{PROJECT}/{env}/{name}"


@dataclass(frozen=True)
class EnvConfig:
    name: str
    api_desired_count: int
    api_max_count: int
    ingest_schedule: str
    ingest_schedule_enabled: bool
    alarms: bool


@dataclass(frozen=True)
class Settings:
    github_repo: str
    create_github_oidc_provider: bool
    certificate_arn: str
    staging_allowed_cidrs: list[str]
    waf_chat_rate_limit: int
    waf_chat_rate_action: str
    db_instance_type: str
    datagpt_ui_image: str
    datagpt_ui_port: int
    staging: EnvConfig
    prod: EnvConfig


def load_settings(scope: Construct) -> Settings:
    ctx = scope.node.try_get_context

    def env(name: str) -> EnvConfig:
        c = ctx(name) or {}
        if isinstance(c, str):  # `-c staging={...}` arrives as a JSON string
            c = json.loads(c)
        return EnvConfig(
            name=name,
            api_desired_count=int(c.get("apiDesiredCount", 0)),
            api_max_count=int(c.get("apiMaxCount", 1)),
            ingest_schedule=c.get("ingestSchedule", "rate(1 hour)"),
            ingest_schedule_enabled=bool(c.get("ingestScheduleEnabled", False)),
            alarms=bool(c.get("alarms", False)),
        )

    action = str(ctx("wafChatRateAction") or "COUNT").upper()
    if action not in ("COUNT", "BLOCK"):
        raise ValueError("wafChatRateAction must be COUNT or BLOCK")

    return Settings(
        github_repo=ctx("githubRepo"),
        create_github_oidc_provider=bool(ctx("createGithubOidcProvider")),
        # Contains the account ID, so it comes from the environment (a GitHub
        # Actions variable in CI) rather than cdk.json in this public repo.
        # Listeners depend on it, so every deploy must use the same value.
        certificate_arn=ctx("certificateArn") or os.environ.get("CERTIFICATE_ARN", ""),
        staging_allowed_cidrs=list(ctx("stagingAllowedCidrs") or ["0.0.0.0/0"]),
        waf_chat_rate_limit=int(ctx("wafChatRateLimitPer5Min") or 300),
        waf_chat_rate_action=action,
        db_instance_type=ctx("dbInstanceType") or "t4g.small",
        datagpt_ui_image=ctx("datagptUiImage")
        or os.environ.get("DATAGPT_UI_IMAGE", ""),
        datagpt_ui_port=int(ctx("datagptUiPort") or 3001),
        staging=env("staging"),
        prod=env("prod"),
    )
