"""Settings shared by all stacks.

Values that differ per deployment come from cdk.json context so they can be
overridden with `-c key=value`. Nothing secret lives here: secrets are SSM
SecureString parameters created outside CDK (see scripts/copy_params.sh).
Values that contain the account ID come from environment variables (GitHub
Actions variables in CI), because this repository is public.
"""

import ipaddress
import json
import os
from dataclasses import dataclass

from constructs import Construct

PROJECT = "datagovmy-ai"
REGION = "ap-southeast-5"
AZS = ["ap-southeast-5a", "ap-southeast-5b"]
VPC_CIDR = "10.40.0.0/16"

# The existing Singapore ALB stays in front (ai.datagovmy.app points at it) and
# forwards to Malaysia over VPC peering until the DNS record is moved
EDGE_REGION = "ap-southeast-1"

# Placeholder account for synth without credentials (CI on pull requests).
# A deploy with it fails: the CLI refuses credentials for any other account.
PLACEHOLDER_ACCOUNT = "000000000000"

# CDK bootstrap qualifier (default); used to scope CI roles to the bootstrap roles
CDK_QUALIFIER = "hnb659fds"

# SSM SecureString parameters, read from /datagovmy-ai/<env>/<NAME>.
# DATABASE_URL* are created by db_bootstrap.py, the rest are copied from
# Singapore by scripts/copy_params.sh.
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

API_PORT = 8000  # container port
# Ports on the internal NLB in Malaysia, which the Singapore ALB targets
NLB_PORTS = {"prod": 8000, "staging": 8001}
# Header that sends a request to the new stack whatever the cutover weight,
# so it can be tested through the real hostname before any users move
TEST_HEADER = ("x-datagovmy-backend", "ecs")


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
class EdgeConfig:
    """Existing Singapore resources the edge stack attaches to (EDGE_CONFIG)"""

    vpc_id: str
    vpc_cidr: str
    route_table_ids: list[str]
    alb_arn: str
    alb_security_group_id: str
    certificate_arn: str
    # by listener port ("443", "8005", "3001")
    listeners: dict[str, str]
    legacy_target_groups: dict[str, str]
    # Old host's /auth-token over the peering (private IP), so the new stack
    # can forward token rolls to it during the cutover
    legacy_auth_token_url: str = ""
    placeholder: bool = False


PLACEHOLDER_EDGE = EdgeConfig(
    vpc_id="vpc-00000000000000000",
    vpc_cidr="172.31.0.0/16",
    route_table_ids=["rtb-00000000000000000"],
    alb_arn=f"arn:aws:elasticloadbalancing:{EDGE_REGION}:{PLACEHOLDER_ACCOUNT}:loadbalancer/app/placeholder/0000000000000000",
    alb_security_group_id="sg-00000000000000000",
    certificate_arn=f"arn:aws:acm:{EDGE_REGION}:{PLACEHOLDER_ACCOUNT}:certificate/00000000-0000-0000-0000-000000000000",
    listeners={
        port: f"arn:aws:elasticloadbalancing:{EDGE_REGION}:{PLACEHOLDER_ACCOUNT}:listener/app/placeholder/0000000000000000/000000000000{port:0>4}"
        for port in ("443", "8005", "3001")
    },
    legacy_target_groups={
        port: f"arn:aws:elasticloadbalancing:{EDGE_REGION}:{PLACEHOLDER_ACCOUNT}:targetgroup/placeholder-{port}/0000000000000000"
        for port in ("443", "8005", "3001")
    },
    placeholder=True,
)


@dataclass(frozen=True)
class Settings:
    account: str
    github_repo: str
    create_github_oidc_provider: bool
    public_alb: bool
    certificate_arn: str
    staging_allowed_cidrs: list[str]
    waf_mode: str
    waf_chat_rate_limit: int
    waf_generate_meta_rate_limit: int
    db_instance_type: str
    datagpt_ui_image: str
    datagpt_ui_port: int
    edge: EdgeConfig
    # percent of traffic the Singapore ALB sends to the new stack, by service
    edge_weights: dict[str, int]
    # send the token-roll Lambda's /auth-token calls to the new stack (which
    # forwards them to the old host); on before any weight is raised
    edge_auth_token_to_new: bool
    staging: EnvConfig
    prod: EnvConfig


# App subnets as CDK allocates them from VPC_CIDR (public /24s, then app /22s,
# then db /24s). NetworkStack checks the real allocation against this.
APP_SUBNET_CIDRS = ["10.40.4.0/22", "10.40.8.0/22"]


def nlb_ips() -> list[str]:
    """Fixed private IPs of the internal NLB, one per app subnet. Known at
    synth time, so the Singapore stack can target them without a
    cross-region reference."""
    return [str(ipaddress.ip_network(cidr)[10]) for cidr in APP_SUBNET_CIDRS]


def load_settings(scope: Construct) -> Settings:
    ctx = scope.node.try_get_context
    account = os.environ.get("CDK_DEPLOY_ACCOUNT") or PLACEHOLDER_ACCOUNT

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

    waf_mode = str(ctx("wafMode") or "COUNT").upper()
    if waf_mode not in ("COUNT", "BLOCK"):
        raise ValueError("wafMode must be COUNT or BLOCK")

    raw_edge = os.environ.get("EDGE_CONFIG")
    if raw_edge:
        e = json.loads(raw_edge)
        edge = EdgeConfig(
            vpc_id=e["vpcId"],
            vpc_cidr=e["vpcCidr"],
            route_table_ids=e["routeTableIds"],
            alb_arn=e["albArn"],
            alb_security_group_id=e["albSecurityGroupId"],
            certificate_arn=e["certificateArn"],
            listeners=e["listeners"],
            legacy_target_groups=e["legacyTargetGroups"],
            legacy_auth_token_url=e.get("legacyAuthTokenUrl", ""),
        )
    elif account != PLACEHOLDER_ACCOUNT:
        raise ValueError("EDGE_CONFIG must be set to deploy (see infra/README.md)")
    else:
        edge = PLACEHOLDER_EDGE

    weights = ctx("edgeWeights") or {}
    if isinstance(weights, str):
        weights = json.loads(weights)
    weights = {k: int(v) for k, v in weights.items()}
    if any(not 0 <= v <= 100 for v in weights.values()):
        raise ValueError("edgeWeights are percentages (0-100)")
    auth_token_to_new = bool(ctx("edgeAuthTokenToNew"))
    if weights.get("api", 0) > 0 and not auth_token_to_new:
        # Otherwise a token roll reaches only the old host, and users routed
        # to the new stack get 401s until the next roll
        raise ValueError("raise edgeWeights.api only with edgeAuthTokenToNew: true")
    if auth_token_to_new and not edge.legacy_auth_token_url:
        raise ValueError("edgeAuthTokenToNew needs EDGE_CONFIG.legacyAuthTokenUrl")

    return Settings(
        account=account,
        github_repo=ctx("githubRepo"),
        create_github_oidc_provider=bool(ctx("createGithubOidcProvider")),
        # Malaysia's own public ALB, for after ai.datagovmy.app is moved
        public_alb=bool(ctx("publicAlb")),
        # Contains the account ID, so it comes from the environment. Only used
        # with publicAlb; listeners depend on it, so keep it stable.
        certificate_arn=ctx("certificateArn") or os.environ.get("CERTIFICATE_ARN", ""),
        staging_allowed_cidrs=list(ctx("stagingAllowedCidrs") or ["0.0.0.0/0"]),
        waf_mode=waf_mode,
        waf_chat_rate_limit=int(ctx("wafChatRateLimitPer5Min") or 300),
        waf_generate_meta_rate_limit=int(ctx("wafGenerateMetaRateLimitPer5Min") or 30),
        db_instance_type=ctx("dbInstanceType") or "t4g.small",
        datagpt_ui_image=ctx("datagptUiImage")
        or os.environ.get("DATAGPT_UI_IMAGE", ""),
        datagpt_ui_port=int(ctx("datagptUiPort") or 3001),
        edge=edge,
        edge_weights={"api": weights.get("api", 0), "ui": weights.get("ui", 0)},
        edge_auth_token_to_new=auth_token_to_new,
        staging=env("staging"),
        prod=env("prod"),
    )
