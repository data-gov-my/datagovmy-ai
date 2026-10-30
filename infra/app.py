#!/usr/bin/env python3
"""datagovmy-ai platform in ap-southeast-5, fronted by the existing Singapore
ALB until ai.datagovmy.app is moved. See README.md."""

import aws_cdk as cdk

from stacks.ci import CiStack
from stacks.config import EDGE_REGION, PROJECT, REGION, load_settings
from stacks.data import DataStack
from stacks.edge import EdgeStack
from stacks.network import NetworkStack
from stacks.platform import PlatformStack
from stacks.service import ServiceStack

app = cdk.App()
settings = load_settings(app)

# The account isn't committed to this public repo: it comes from
# CDK_DEPLOY_ACCOUNT, and the CLI refuses credentials for any other account.
# Without it, synth uses a placeholder account and needs no credentials (CI on
# pull requests); deploying that fails.
env = cdk.Environment(account=settings.account, region=REGION)
edge_env = cdk.Environment(account=settings.account, region=EDGE_REGION)

# The peering ID crosses regions (Network → Edge)
network = NetworkStack(
    app,
    "DatagovmyAi-Network",
    settings=settings,
    env=env,
    cross_region_references=True,
)
data = DataStack(app, "DatagovmyAi-Data", settings=settings, network=network, env=env)
platform = PlatformStack(
    app, "DatagovmyAi-Platform", settings=settings, network=network, env=env
)
for env_config in (settings.staging, settings.prod):
    ServiceStack(
        app,
        f"DatagovmyAi-Service-{env_config.name}",
        settings=settings,
        env_config=env_config,
        network=network,
        data=data,
        platform=platform,
        env=env,
    )
EdgeStack(
    app,
    "DatagovmyAi-Edge",
    settings=settings,
    network=network,
    env=edge_env,
    cross_region_references=True,
)
CiStack(app, "DatagovmyAi-Ci", settings=settings, env=env)

cdk.Tags.of(app).add("Project", PROJECT)
cdk.Tags.of(app).add("ManagedBy", "cdk")

app.synth()
