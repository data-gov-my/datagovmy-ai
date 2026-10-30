from aws_cdk import Duration, RemovalPolicy, Stack, aws_ec2 as ec2, aws_rds as rds
from constructs import Construct

from .config import PROJECT, Settings
from .network import NetworkStack


class DataStack(Stack):
    """PostgreSQL with pgvector: vectors and the ingest record manager in one
    database, so they can't drift apart.

    Staging uses its own database and users on the same instance. The pgvector
    extension, databases and app users are created by the DB bootstrap, not here.
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

        self.db = rds.DatabaseInstance(
            self,
            "Db",
            engine=rds.DatabaseInstanceEngine.postgres(
                version=rds.PostgresEngineVersion.of("17", "17")
            ),
            instance_type=ec2.InstanceType(settings.db_instance_type),
            vpc=network.vpc,
            vpc_subnets=ec2.SubnetSelection(
                subnet_type=ec2.SubnetType.PRIVATE_ISOLATED
            ),
            security_groups=[network.db_sg],
            credentials=rds.Credentials.from_generated_secret(
                "dgmyai_admin", secret_name=f"{PROJECT}/rds-admin"
            ),
            database_name="dgmyai",
            allocated_storage=20,
            max_allocated_storage=100,
            storage_type=rds.StorageType.GP3,
            storage_encrypted=True,
            multi_az=False,
            publicly_accessible=False,
            backup_retention=Duration.days(7),
            deletion_protection=True,
            removal_policy=RemovalPolicy.SNAPSHOT,
            auto_minor_version_upgrade=True,
            cloudwatch_logs_exports=["postgresql"],
        )
