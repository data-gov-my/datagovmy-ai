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
    aws_wafv2 as wafv2,
)
from constructs import Construct

from .config import DISCORD_WEBHOOK_PARAM, PROJECT, Settings
from .network import NetworkStack

LAMBDA_DIR = Path(__file__).resolve().parents[1] / "lambdas"

# ELBSecurityPolicy-TLS13-1-2-2021-06, the policy on the Singapore ALB's HTTPS
# listeners, so existing clients negotiate TLS exactly as before
TLS_POLICY = elbv2.SslPolicy.RECOMMENDED_TLS


class PlatformStack(Stack):
    """Resources shared by staging and prod: image repo, cluster, ALB, WAF and
    alerting. Services attach to the listeners from their own stacks."""

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

        # Prod mirrors the Singapore ALB's ports. Without a certificate (before
        # cutover) prod and staging are reachable over plain HTTP on the ALB
        # DNS name only.
        if settings.certificate_arn:
            cert = acm.Certificate.from_certificate_arn(
                self, "Cert", settings.certificate_arn
            )
            self.prod_listeners = [
                listener("Https", 443, True),
                listener("Https8005", 8005, True),
            ]
            self.staging_listener = listener("StagingHttps", 8443, True)
            self.alb.add_listener(
                "HttpRedirect",
                port=80,
                open=False,
                default_action=elbv2.ListenerAction.redirect(
                    protocol="HTTPS", port="443", permanent=True
                ),
            )
        else:
            cert = None
            self.prod_listeners = [listener("Http", 80, False)]
            self.staging_listener = listener("StagingHttp", 8080, False)

        self.ui_listener = listener("Ui", settings.datagpt_ui_port, False)

        self._waf(settings)

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

        CfnOutput(self, "AlbDnsName", value=self.alb.load_balancer_dns_name)
        CfnOutput(self, "RepoUri", value=self.repo.repository_uri)

    def _waf(self, settings: Settings) -> None:
        def visibility(name: str):
            return wafv2.CfnWebACL.VisibilityConfigProperty(
                cloud_watch_metrics_enabled=True,
                metric_name=name,
                sampled_requests_enabled=True,
            )

        rate_action = (
            wafv2.CfnWebACL.RuleActionProperty(block={})
            if settings.waf_chat_rate_action == "BLOCK"
            else wafv2.CfnWebACL.RuleActionProperty(count={})
        )

        acl = wafv2.CfnWebACL(
            self,
            "WebAcl",
            scope="REGIONAL",
            default_action=wafv2.CfnWebACL.DefaultActionProperty(allow={}),
            visibility_config=visibility(f"{PROJECT}-acl"),
            rules=[
                wafv2.CfnWebACL.RuleProperty(
                    name="common",
                    priority=0,
                    override_action=wafv2.CfnWebACL.OverrideActionProperty(none={}),
                    statement=wafv2.CfnWebACL.StatementProperty(
                        managed_rule_group_statement=wafv2.CfnWebACL.ManagedRuleGroupStatementProperty(
                            vendor_name="AWS",
                            name="AWSManagedRulesCommonRuleSet",
                            # Chat history and /generate-meta payloads exceed the
                            # rule's 8 KB body limit; count instead of block
                            rule_action_overrides=[
                                wafv2.CfnWebACL.RuleActionOverrideProperty(
                                    name="SizeRestrictions_BODY",
                                    action_to_use=wafv2.CfnWebACL.RuleActionProperty(
                                        count={}
                                    ),
                                )
                            ],
                        )
                    ),
                    visibility_config=visibility("common"),
                ),
                # Starts in COUNT: if the frontend calls the API from its own
                # servers, every user shares a few IPs and a per-IP limit would
                # throttle them all. Check the WAF metrics before switching to BLOCK.
                wafv2.CfnWebACL.RuleProperty(
                    name="chat-rate",
                    priority=1,
                    action=rate_action,
                    statement=wafv2.CfnWebACL.StatementProperty(
                        rate_based_statement=wafv2.CfnWebACL.RateBasedStatementProperty(
                            limit=settings.waf_chat_rate_limit,
                            aggregate_key_type="IP",
                            scope_down_statement=wafv2.CfnWebACL.StatementProperty(
                                byte_match_statement=wafv2.CfnWebACL.ByteMatchStatementProperty(
                                    field_to_match=wafv2.CfnWebACL.FieldToMatchProperty(
                                        uri_path={}
                                    ),
                                    positional_constraint="STARTS_WITH",
                                    search_string="/chat",
                                    text_transformations=[
                                        wafv2.CfnWebACL.TextTransformationProperty(
                                            priority=0, type="LOWERCASE"
                                        )
                                    ],
                                )
                            ),
                        )
                    ),
                    visibility_config=visibility("chat-rate"),
                ),
            ],
        )
        wafv2.CfnWebACLAssociation(
            self,
            "WebAclAssociation",
            resource_arn=self.alb.load_balancer_arn,
            web_acl_arn=acl.attr_arn,
        )
