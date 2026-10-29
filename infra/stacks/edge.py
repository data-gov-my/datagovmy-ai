from aws_cdk import (
    Annotations,
    Duration,
    Stack,
    aws_ec2 as ec2,
    aws_elasticloadbalancingv2 as elbv2,
    aws_elasticloadbalancingv2_targets as targets,
)
from constructs import Construct

from .config import NLB_PORTS, TEST_HEADER, VPC_CIDR, Settings, nlb_ips
from .network import NetworkStack
from .waf import ApiWebAcl


class EdgeStack(Stack):
    """Singapore (ap-southeast-1): attaches the existing ALB that
    ai.datagovmy.app points at to the new stack, so the cutover needs no DNS
    change.

    The ALB, its listeners, certificate and legacy target groups were made in
    the console and stay that way; this stack only adds to them:

    - a route to the Malaysia VPC over the peering (NetworkStack)
    - target groups whose targets are the Malaysia NLB's fixed private IPs
    - on each existing listener, rules that split traffic between the legacy
      target group and Malaysia by `edgeWeights` (0 = all legacy, the
      starting point), plus a rule that sends requests carrying TEST_HEADER
      to Malaysia whatever the weight
    - a staging listener on 8443
    - the WAF (all rules counting until `wafMode` is BLOCK)

    The listeners' default actions are untouched: deleting this stack puts
    the ALB back exactly as it was.
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
        edge = settings.edge

        for i, route_table_id in enumerate(edge.route_table_ids):
            ec2.CfnRoute(
                self,
                f"PeeringRoute{i}",
                route_table_id=route_table_id,
                destination_cidr_block=VPC_CIDR,
                vpc_peering_connection_id=network.peering.ref,
            )

        vpc = ec2.Vpc.from_vpc_attributes(
            self,
            "Vpc",
            vpc_id=edge.vpc_id,
            vpc_cidr_block=edge.vpc_cidr,
            availability_zones=["ap-southeast-1a", "ap-southeast-1b"],
        )
        alb_sg = ec2.SecurityGroup.from_security_group_id(
            self, "AlbSg", edge.alb_security_group_id, mutable=False
        )

        def target_group(name: str, port: int, health_path: str, codes: str):
            return elbv2.ApplicationTargetGroup(
                self,
                f"{name}Tg",
                vpc=vpc,
                port=port,
                protocol=elbv2.ApplicationProtocol.HTTP,
                target_type=elbv2.TargetType.IP,
                # Outside this VPC (peered), so the AZ must be "all"
                targets=[
                    targets.IpTarget(ip, port=port, availability_zone="all")
                    for ip in nlb_ips()
                ],
                deregistration_delay=Duration.seconds(120),
                health_check=elbv2.HealthCheck(
                    path=health_path,
                    healthy_http_codes=codes,
                    interval=Duration.seconds(15),
                ),
            )

        imported: dict[str, elbv2.IApplicationListener] = {}

        def listener(port: str) -> elbv2.IApplicationListener:
            if port not in imported:
                imported[
                    port
                ] = elbv2.ApplicationListener.from_application_listener_attributes(
                    self,
                    f"Listener{port}",
                    listener_arn=edge.listeners[port],
                    security_group=alb_sg,
                )
            return imported[port]

        def split(port: str, new_tg: elbv2.ApplicationTargetGroup, weight: int):
            legacy = elbv2.ApplicationTargetGroup.from_target_group_attributes(
                self, f"Legacy{port}", target_group_arn=edge.legacy_target_groups[port]
            )
            # CDK can't add security group rules for an imported target group;
            # none are needed, the ALB's security group allows all egress
            Annotations.of(legacy).acknowledge_warning(
                "@aws-cdk/aws-elbv2:albTargetGroupCannotRegisterListener",
                "Existing ALB security group allows all egress",
            )
            l = listener(port)
            elbv2.ApplicationListenerRule(
                self,
                f"Test{port}",
                listener=l,
                priority=10,
                conditions=[
                    elbv2.ListenerCondition.http_header(
                        TEST_HEADER[0], [TEST_HEADER[1]]
                    )
                ],
                action=elbv2.ListenerAction.forward([new_tg]),
            )
            elbv2.ApplicationListenerRule(
                self,
                f"Split{port}",
                listener=l,
                priority=20,
                conditions=[elbv2.ListenerCondition.path_patterns(["/*"])],
                action=elbv2.ListenerAction.weighted_forward(
                    [
                        elbv2.WeightedTargetGroup(
                            target_group=legacy, weight=100 - weight
                        ),
                        elbv2.WeightedTargetGroup(target_group=new_tg, weight=weight),
                    ]
                ),
            )

        api = target_group("ProdApi", NLB_PORTS["prod"], "/health", "200")
        # 8005 is what the frontend uses; 443 goes to the dead :8000 group today
        for port in ("8005", "443"):
            split(port, api, settings.edge_weights["api"])
            # Token rolls go to the new stack, which forwards them to the old
            # host, so both accept the current token whatever the split
            if settings.edge_auth_token_to_new:
                elbv2.ApplicationListenerRule(
                    self,
                    f"AuthToken{port}",
                    listener=listener(port),
                    priority=15,
                    conditions=[elbv2.ListenerCondition.path_patterns(["/auth-token"])],
                    action=elbv2.ListenerAction.forward([api]),
                )

        if settings.datagpt_ui_image:
            ui_port = settings.datagpt_ui_port
            ui = target_group("Ui", ui_port, "/", "200-399")
            split(str(ui_port), ui, settings.edge_weights["ui"])

        # Staging: https://ai.datagovmy.app:8443, same certificate
        staging = target_group("StagingApi", NLB_PORTS["staging"], "/health", "200")
        elbv2.ApplicationListener(
            self,
            "StagingListener",
            load_balancer=elbv2.ApplicationLoadBalancer.from_application_load_balancer_attributes(
                self,
                "Alb",
                load_balancer_arn=edge.alb_arn,
                security_group_id=edge.alb_security_group_id,
            ),
            port=8443,
            protocol=elbv2.ApplicationProtocol.HTTPS,
            certificates=[elbv2.ListenerCertificate.from_arn(edge.certificate_arn)],
            # Matches the existing HTTPS listeners
            ssl_policy=elbv2.SslPolicy.RECOMMENDED_TLS,
            default_action=elbv2.ListenerAction.forward([staging]),
            open=False,
        )
        for i, cidr in enumerate(settings.staging_allowed_cidrs):
            ec2.CfnSecurityGroupIngress(
                self,
                f"StagingIngress{i}",
                group_id=edge.alb_security_group_id,
                ip_protocol="tcp",
                from_port=8443,
                to_port=8443,
                cidr_ip=cidr,
                description="datagovmy-ai staging (CDK EdgeStack)",
            )

        ApiWebAcl(self, "Waf", settings=settings, alb_arn=edge.alb_arn)
