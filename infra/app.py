#!/usr/bin/env python3
"""datagovmy-ai platform in ap-southeast-5. See README.md."""

import os

import aws_cdk as cdk

from stacks.ci import CiStack
from stacks.config import PROJECT, REGION, load_settings
from stacks.data import DataStack
from stacks.network import NetworkStack
from stacks.platform import PlatformStack
from stacks.service import ServiceStack

app = cdk.App()
settings = load_settings(app)

# The account isn't committed to this public repo. Set CDK_DEPLOY_ACCOUNT to
# deploy: the CLI then refuses credentials for any other account. Unset, synth
# is account-agnostic and needs no credentials (CI checks on pull requests).
env = cdk.Environment(account=os.environ.get("CDK_DEPLOY_ACCOUNT"), region=REGION)

network = NetworkStack(app, "DatagovmyAi-Network", settings=settings, env=env)
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
CiStack(app, "DatagovmyAi-Ci", settings=settings, env=env)

cdk.Tags.of(app).add("Project", PROJECT)
cdk.Tags.of(app).add("ManagedBy", "cdk")

app.synth()
