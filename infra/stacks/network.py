from aws_cdk import Stack, aws_ec2 as ec2
from constructs import Construct

from .config import (
    API_PORT,
    APP_SUBNET_CIDRS,
    AZS,
    EDGE_REGION,
    NLB_PORTS,
    VPC_CIDR,
    Settings,
)


class NetworkStack(Stack):
    """VPC, peering to the Singapore VPC, and the security groups shared by
    the load balancers, tasks and database.

    All security groups live here so the rules between them don't create
    cross-stack cycles.
    """

    @property
    def availability_zones(self) -> list[str]:
        # Fixed, so synth never looks the account's AZs up (no credentials in CI)
        return AZS

    def __init__(
        self, scope: Construct, id: str, *, settings: Settings, **kwargs
    ) -> None:
        super().__init__(scope, id, **kwargs)
        edge = settings.edge
        ui_port = settings.datagpt_ui_port

        self.vpc = ec2.Vpc(
            self,
            "Vpc",
            ip_addresses=ec2.IpAddresses.cidr(VPC_CIDR),
            # Explicit, so synth needs no AZ lookup
            availability_zones=AZS,
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
        self.app_subnets = self.vpc.select_subnets(
            subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS
        ).subnets
        actual = [s.ipv4_cidr_block for s in self.app_subnets]
        if actual != APP_SUBNET_CIDRS:
            raise ValueError(
                f"app subnets are {actual}, config.APP_SUBNET_CIDRS says "
                f"{APP_SUBNET_CIDRS}: update it (the NLB IPs derive from it)"
            )

        # Singapore ALB → this VPC. Same account, so CloudFormation accepts the
        # peering itself. Only the app subnets (internal NLB) route back.
        self.peering = ec2.CfnVPCPeeringConnection(
            self,
            "EdgePeering",
            vpc_id=self.vpc.vpc_id,
            peer_vpc_id=edge.vpc_id,
            peer_region=EDGE_REGION,
        )
        for i, subnet in enumerate(self.app_subnets):
            ec2.CfnRoute(
                self,
                f"EdgeRoute{i}",
                route_table_id=subnet.route_table.route_table_id,
                destination_cidr_block=edge.vpc_cidr,
                vpc_peering_connection_id=self.peering.ref,
            )

        # Internal NLB: only the Singapore VPC (its ALB) can reach it
        self.nlb_sg = ec2.SecurityGroup(
            self, "NlbSg", vpc=self.vpc, description="Internal NLB (Singapore ALB)"
        )
        for port in [*NLB_PORTS.values(), ui_port]:
            self.nlb_sg.add_ingress_rule(
                ec2.Peer.ipv4(edge.vpc_cidr), ec2.Port.tcp(port), f"edge {port}"
            )

        self.app_sg = ec2.SecurityGroup(
            self, "AppSg", vpc=self.vpc, description="ECS tasks (api, ui, ingest)"
        )
        self.app_sg.add_ingress_rule(
            self.nlb_sg, ec2.Port.tcp(API_PORT), "api from NLB"
        )
        self.app_sg.add_ingress_rule(self.nlb_sg, ec2.Port.tcp(ui_port), "ui from NLB")

        # Malaysia's own public ALB, for after the DNS move (publicAlb)
        self.alb_sg = None
        if settings.public_alb:
            self.alb_sg = ec2.SecurityGroup(
                self, "AlbSg", vpc=self.vpc, description="Public ALB"
            )
            # Same public ports as the Singapore ALB, so clients need no change
            for port in (80, 443, 8005, ui_port):
                self.alb_sg.add_ingress_rule(
                    ec2.Peer.any_ipv4(), ec2.Port.tcp(port), f"public {port}"
                )
            for cidr in settings.staging_allowed_cidrs:
                self.alb_sg.add_ingress_rule(
                    ec2.Peer.ipv4(cidr), ec2.Port.tcp(8443), "staging"
                )
            self.app_sg.add_ingress_rule(
                self.alb_sg, ec2.Port.tcp(API_PORT), "api from ALB"
            )
            self.app_sg.add_ingress_rule(
                self.alb_sg, ec2.Port.tcp(ui_port), "ui from ALB"
            )

        self.db_sg = ec2.SecurityGroup(
            self,
            "DbSg",
            vpc=self.vpc,
            description="RDS PostgreSQL",
            allow_all_outbound=False,
        )
        self.db_sg.add_ingress_rule(self.app_sg, ec2.Port.tcp(5432), "tasks to db")
