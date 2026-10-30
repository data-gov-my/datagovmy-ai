from pathlib import Path

from aws_cdk import (
    Aws,
    CfnOutput,
    Duration,
    RemovalPolicy,
    Stack,
    aws_certificatemanager as acm,
    aws_ec2 as ec2,
    aws_ecr as ecr,
    aws_ecs as ecs,
    aws_elasticloadbalancingv2 as elbv2,
    aws_iam as iam,
    aws_lambda as lambda_,
    aws_logs as logs,
    aws_sns as sns,
    aws_sns_subscriptions as subs,
)
from constructs import Construct

from .config import DISCORD_WEBHOOK_PARAM, PROJECT, Settings, nlb_ips
from .network import NetworkStack
from .waf import ApiWebAcl

LAMBDA_DIR = Path(__file__).resolve().parents[1] / "lambdas"

# ELBSecurityPolicy-TLS13-1-2-2021-06, the policy on the Singapore ALB's HTTPS
# listeners, so existing clients negotiate TLS exactly as before
TLS_POLICY = elbv2.SslPolicy.RECOMMENDED_TLS


class PlatformStack(Stack):
    """Resources shared by staging and prod: image repo, cluster, load
    balancers and alerting. Services attach listeners/rules from their own
    stacks.

    Traffic enters through the Singapore ALB (EdgeStack), which forwards over
    VPC peering to the internal NLB here. With `publicAlb` the stack also gets
    its own public ALB and WAF, for after ai.datagovmy.app is moved.
    """

    def __init__(
        self,
        scope: Construct,
        id: str,
        *,
        settings: Settings,
        network: NetworkStack,
        **kwargs,
    ) -> None:
        super().__init__(scope, id, **kwargs)

        self.repo = ecr.Repository(
            self,
            "Repo",
            repository_name=PROJECT,
            image_tag_mutability=ecr.TagMutability.IMMUTABLE,
            image_scan_on_push=True,
            removal_policy=RemovalPolicy.RETAIN,
            lifecycle_rules=[ecr.LifecycleRule(max_image_count=30)],
        )

        self.cluster = ecs.Cluster(
            self, "Cluster", vpc=network.vpc, cluster_name=PROJECT
        )

        # Fixed private IPs: the Singapore ALB registers them as targets
        self.nlb = elbv2.NetworkLoadBalancer(
            self,
            "Nlb",
            vpc=network.vpc,
            internet_facing=False,
            security_groups=[network.nlb_sg],
            cross_zone_enabled=True,  # staging runs one task, in one AZ
            subnet_mappings=[
                elbv2.SubnetMapping(subnet=subnet, private_ipv4_address=ip)
                for subnet, ip in zip(network.app_subnets, nlb_ips())
            ],
        )

        self.alb = None
        if settings.public_alb:
            self._public_alb(settings, network)

        # Alerts: SNS → Discord. Services publish alarms and ingest failures here.
        self.alerts = sns.Topic(self, "Alerts", topic_name=f"{PROJECT}-alerts")
        notifier = lambda_.Function(
            self,
            "DiscordNotifier",
            runtime=lambda_.Runtime.PYTHON_3_12,
            architecture=lambda_.Architecture.ARM_64,
            handler="index.handler",
            code=lambda_.Code.from_asset(str(LAMBDA_DIR / "discord_notify")),
            timeout=Duration.seconds(10),
            environment={"WEBHOOK_PARAM": DISCORD_WEBHOOK_PARAM},
            log_group=logs.LogGroup(
                self, "DiscordNotifierLogs", retention=logs.RetentionDays.ONE_MONTH
            ),
        )
        notifier.add_to_role_policy(
            iam.PolicyStatement(
                actions=["ssm:GetParameter"],
                resources=[
                    f"arn:aws:ssm:{Aws.REGION}:{Aws.ACCOUNT_ID}:parameter{DISCORD_WEBHOOK_PARAM}"
                ],
            )
        )
        self.alerts.add_subscription(subs.LambdaSubscription(notifier))

        CfnOutput(self, "NlbIps", value=",".join(nlb_ips()))
        CfnOutput(self, "RepoUri", value=self.repo.repository_uri)

    def _public_alb(self, settings: Settings, network: NetworkStack) -> None:
        if not settings.certificate_arn:
            raise ValueError("publicAlb needs CERTIFICATE_ARN (ACM, ap-southeast-5)")
        cert = acm.Certificate.from_certificate_arn(
            self, "Cert", settings.certificate_arn
        )
        self.alb = elbv2.ApplicationLoadBalancer(
            self,
            "Alb",
            vpc=network.vpc,
            internet_facing=True,
            security_group=network.alb_sg,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PUBLIC),
            # /chat/stream answers can run long; keep above the slowest stream
            idle_timeout=Duration.seconds(300),
        )
        not_found = elbv2.ListenerAction.fixed_response(
            404, content_type="text/plain", message_body="Not found"
        )

        def listener(name: str, port: int, https: bool) -> elbv2.ApplicationListener:
            return self.alb.add_listener(
                name,
                port=port,
                protocol=(
                    elbv2.ApplicationProtocol.HTTPS
                    if https
                    else elbv2.ApplicationProtocol.HTTP
                ),
                certificates=[cert] if https else None,
                ssl_policy=TLS_POLICY if https else None,
                default_action=not_found,
                open=False,  # ingress is managed in NetworkStack
            )

        # Mirrors the Singapore ALB's ports
        self.prod_listeners = [
            listener("Https", 443, True),
            listener("Https8005", 8005, True),
        ]
        self.staging_listener = listener("StagingHttps", 8443, True)
        self.ui_listener = listener("Ui", settings.datagpt_ui_port, False)
        self.alb.add_listener(
            "HttpRedirect",
            port=80,
            open=False,
            default_action=elbv2.ListenerAction.redirect(
                protocol="HTTPS", port="443", permanent=True
            ),
        )
        ApiWebAcl(self, "Waf", settings=settings, alb_arn=self.alb.load_balancer_arn)
        CfnOutput(self, "AlbDnsName", value=self.alb.load_balancer_dns_name)
