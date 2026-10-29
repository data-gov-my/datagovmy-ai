from aws_cdk import Stack, aws_ec2 as ec2
from constructs import Construct

from .config import API_PORT, Settings


class NetworkStack(Stack):
    """VPC and the security groups shared by the ALB, tasks and database.

    All three security groups live here so the rules between them don't create
    cross-stack cycles.
    """

    def __init__(
        self, scope: Construct, id: str, *, settings: Settings, **kwargs
    ) -> None:
        super().__init__(scope, id, **kwargs)

        self.vpc = ec2.Vpc(
            self,
            "Vpc",
            ip_addresses=ec2.IpAddresses.cidr("10.40.0.0/16"),
            max_azs=2,
            nat_gateways=1,
            subnet_configuration=[
                ec2.SubnetConfiguration(
                    name="public", subnet_type=ec2.SubnetType.PUBLIC, cidr_mask=24
                ),
                ec2.SubnetConfiguration(
                    name="app",
                    subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS,
                    cidr_mask=22,
                ),
                ec2.SubnetConfiguration(
                    name="db",
                    subnet_type=ec2.SubnetType.PRIVATE_ISOLATED,
                    cidr_mask=24,
                ),
            ],
            gateway_endpoints={
                # ECR image layers come from S3; keep them off the NAT gateway
                "S3": ec2.GatewayVpcEndpointOptions(
                    service=ec2.GatewayVpcEndpointAwsService.S3
                )
            },
        )

        self.alb_sg = ec2.SecurityGroup(
            self, "AlbSg", vpc=self.vpc, description="Public ALB"
        )
        # Same public ports as the Singapore ALB, so the frontend needs no change
        public_ports = [80, 443, 8005, settings.datagpt_ui_port]
        for port in public_ports:
            self.alb_sg.add_ingress_rule(
                ec2.Peer.any_ipv4(), ec2.Port.tcp(port), f"public {port}"
            )
        for cidr in settings.staging_allowed_cidrs:
            for port in (8080, 8443):
                self.alb_sg.add_ingress_rule(
                    ec2.Peer.ipv4(cidr), ec2.Port.tcp(port), f"staging {port}"
                )

        self.app_sg = ec2.SecurityGroup(
            self, "AppSg", vpc=self.vpc, description="ECS tasks (api, ui, ingest)"
        )
        self.app_sg.add_ingress_rule(
            self.alb_sg, ec2.Port.tcp(API_PORT), "api from ALB"
        )
        self.app_sg.add_ingress_rule(
            self.alb_sg, ec2.Port.tcp(settings.datagpt_ui_port), "ui from ALB"
        )

        self.db_sg = ec2.SecurityGroup(
            self,
            "DbSg",
            vpc=self.vpc,
            description="RDS PostgreSQL",
            allow_all_outbound=False,
        )
        self.db_sg.add_ingress_rule(self.app_sg, ec2.Port.tcp(5432), "tasks to db")
