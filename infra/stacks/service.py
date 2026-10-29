from aws_cdk import (
    Aws,
    CfnOutput,
    CfnParameter,
    Duration,
    Stack,
    aws_cloudwatch as cloudwatch,
    aws_cloudwatch_actions as cw_actions,
    aws_ec2 as ec2,
    aws_ecs as ecs,
    aws_elasticloadbalancingv2 as elbv2,
    aws_events as events,
    aws_events_targets as targets,
    aws_iam as iam,
    aws_logs as logs,
    aws_scheduler as scheduler,
    aws_ssm as ssm,
)
from constructs import Construct

from .config import (
    API_PORT,
    API_SECRETS,
    AUTH_TOKEN_PARAM,
    INGEST_SECRETS,
    PROJECT,
    EnvConfig,
    Settings,
    param_name,
)
from .data import DataStack
from .network import NetworkStack
from .platform import PlatformStack


class ServiceStack(Stack):
    """One environment (staging or prod): API service, ingest task and its
    schedule, the one-off DB bootstrap task, plus datagpt-ui for prod.

    The image tag is a CloudFormation parameter, so CI deploys a new image with
    `--parameters ImageTag=<sha>` and an infra-only `cdk deploy` keeps the
    running tag (the CDK CLI reuses previous parameter values).
    """

    def __init__(
        self,
        scope: Construct,
        id: str,
        *,
        settings: Settings,
        env_config: EnvConfig,
        network: NetworkStack,
        data: DataStack,
        platform: PlatformStack,
        **kwargs,
    ) -> None:
        super().__init__(scope, id, **kwargs)
        self.env_name = env = env_config.name
        self.network = network
        self.data = data
        self.platform = platform
        self.prefix = f"{PROJECT}-{env}"

        image_tag = CfnParameter(
            self,
            "ImageTag",
            type="String",
            default="bootstrap",
            description="ECR image tag (git SHA) to run",
        )
        self.image = ecs.ContainerImage.from_ecr_repository(
            platform.repo, tag=image_tag.value_as_string
        )

        self.execution_role = iam.Role(
            self,
            "ExecutionRole",
            role_name=f"{self.prefix}-exec",
            assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AmazonECSTaskExecutionRolePolicy"
                )
            ],
        )
        self.task_role = iam.Role(
            self,
            "TaskRole",
            role_name=f"{self.prefix}-task",
            assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
        )
        # /auth-token writes the rolling token; every task reads it (auth.py,
        # SSMTokenStore)
        self.task_role.add_to_policy(
            iam.PolicyStatement(
                actions=["ssm:GetParameter", "ssm:PutParameter"],
                resources=[
                    f"arn:aws:ssm:{Aws.REGION}:{Aws.ACCOUNT_ID}:parameter"
                    + param_name(env, AUTH_TOKEN_PARAM)
                ],
            )
        )

        api_service = self._api(settings, env_config)
        ingest_family = self._ingest(env_config)
        bootstrap_family = self._db_bootstrap()
        if env == "prod" and settings.datagpt_ui_image:
            self._ui(settings)

        if env_config.alarms:
            self._alarms(api_service)

        CfnOutput(self, "IngestTaskFamily", value=ingest_family)
        CfnOutput(self, "DbBootstrapTaskFamily", value=bootstrap_family)
        CfnOutput(
            self,
            "AppSubnets",
            value=",".join(
                s.subnet_id
                for s in network.vpc.select_subnets(
                    subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS
                ).subnets
            ),
        )
        CfnOutput(self, "AppSecurityGroup", value=network.app_sg.security_group_id)

    def _secrets(self, names: list[str]) -> dict[str, ecs.Secret]:
        if not hasattr(self, "_params"):
            self._params: dict[str, ssm.IParameter] = {}
        for name in names:
            if name not in self._params:
                self._params[
                    name
                ] = ssm.StringParameter.from_secure_string_parameter_attributes(
                    self,
                    f"Param{name}",
                    parameter_name=param_name(self.env_name, name),
                )
        return {
            name: ecs.Secret.from_ssm_parameter(self._params[name]) for name in names
        }

    def _log_group(self, name: str) -> logs.LogGroup:
        return logs.LogGroup(
            self,
            f"{name}Logs",
            log_group_name=f"/ecs/{self.prefix}-{name.lower()}",
            retention=logs.RetentionDays.ONE_MONTH,
        )

    def _task_definition(
        self,
        name: str,
        cpu: int,
        memory: int,
        task_role: iam.IRole | None = None,
        arch: ecs.CpuArchitecture = ecs.CpuArchitecture.ARM64,
    ) -> ecs.FargateTaskDefinition:
        return ecs.FargateTaskDefinition(
            self,
            f"{name}Task",
            family=f"{self.prefix}-{name.lower()}",
            cpu=cpu,
            memory_limit_mib=memory,
            runtime_platform=ecs.RuntimePlatform(
                cpu_architecture=arch,
                operating_system_family=ecs.OperatingSystemFamily.LINUX,
            ),
            execution_role=self.execution_role,
            task_role=task_role or self.task_role,
        )

    def _environment(self) -> dict[str, str]:
        return {
            "ENVIRONMENT": self.env_name,
            "AUTH_TOKEN_PARAM": param_name(self.env_name, AUTH_TOKEN_PARAM),
            "AWS_REGION": Aws.REGION,
        }

    def _api(self, settings: Settings, cfg: EnvConfig) -> ecs.FargateService:
        task = self._task_definition("Api", cpu=512, memory=2048)
        task.add_container(
            "api",
            image=self.image,
            port_mappings=[ecs.PortMapping(container_port=API_PORT)],
            environment=self._environment(),
            secrets=self._secrets(API_SECRETS),
            logging=ecs.LogDrivers.aws_logs(
                stream_prefix="api", log_group=self._log_group("Api")
            ),
            stop_timeout=Duration.seconds(120),
        )

        service = ecs.FargateService(
            self,
            "ApiService",
            service_name=f"{self.prefix}-api",
            cluster=self.platform.cluster,
            task_definition=task,
            desired_count=cfg.api_desired_count,
            security_groups=[self.network.app_sg],
            vpc_subnets=ec2.SubnetSelection(
                subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS
            ),
            min_healthy_percent=100,
            max_healthy_percent=200,
            circuit_breaker=ecs.DeploymentCircuitBreaker(enable=True, rollback=True),
            health_check_grace_period=Duration.seconds(60),
            enable_execute_command=True,
        )
        if cfg.api_desired_count > 0 and cfg.api_max_count > cfg.api_desired_count:
            scaling = service.auto_scale_task_count(
                min_capacity=cfg.api_desired_count, max_capacity=cfg.api_max_count
            )
            scaling.scale_on_cpu_utilization("Cpu", target_utilization_percent=60)

        self.api_tg = elbv2.ApplicationTargetGroup(
            self,
            "ApiTg",
            vpc=self.network.vpc,
            port=API_PORT,
            protocol=elbv2.ApplicationProtocol.HTTP,
            target_type=elbv2.TargetType.IP,
            # let in-flight /chat/stream responses finish during deploys
            deregistration_delay=Duration.seconds(120),
            health_check=elbv2.HealthCheck(
                path="/health",
                healthy_http_codes="200",
                interval=Duration.seconds(15),
                healthy_threshold_count=2,
                unhealthy_threshold_count=3,
            ),
        )
        service.attach_to_application_target_group(self.api_tg)

        listeners = (
            self.platform.prod_listeners
            if self.env_name == "prod"
            else [self.platform.staging_listener]
        )
        for i, listener in enumerate(listeners):
            elbv2.ApplicationListenerRule(
                self,
                f"ApiRule{i}",
                listener=listener,
                priority=100,
                conditions=[elbv2.ListenerCondition.path_patterns(["/*"])],
                action=elbv2.ListenerAction.forward([self.api_tg]),
            )
        return service

    def _ingest(self, cfg: EnvConfig) -> str:
        task = self._task_definition("Ingest", cpu=1024, memory=2048)
        task.add_container(
            "ingest",
            image=self.image,
            command=["python", "ingest.py"],
            environment=self._environment(),
            secrets=self._secrets(INGEST_SECRETS),
            logging=ecs.LogDrivers.aws_logs(
                stream_prefix="ingest", log_group=self._log_group("Ingest")
            ),
        )
        family = f"{self.prefix}-ingest"
        cluster = self.platform.cluster
        subnets = self.network.vpc.select_subnets(
            subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS
        ).subnet_ids

        run_role = iam.Role(
            self,
            "IngestSchedulerRole",
            assumed_by=iam.ServicePrincipal("scheduler.amazonaws.com"),
        )
        run_role.add_to_policy(
            iam.PolicyStatement(
                actions=["ecs:RunTask"],
                resources=[
                    f"arn:aws:ecs:{Aws.REGION}:{Aws.ACCOUNT_ID}:task-definition/{family}:*"
                ],
                conditions={"ArnLike": {"ecs:cluster": cluster.cluster_arn}},
            )
        )
        run_role.add_to_policy(
            iam.PolicyStatement(
                actions=["iam:PassRole"],
                resources=[self.task_role.role_arn, self.execution_role.role_arn],
            )
        )

        scheduler.CfnSchedule(
            self,
            "IngestSchedule",
            name=family,
            schedule_expression=cfg.ingest_schedule,
            flexible_time_window=scheduler.CfnSchedule.FlexibleTimeWindowProperty(
                mode="OFF"
            ),
            state="ENABLED" if cfg.ingest_schedule_enabled else "DISABLED",
            target=scheduler.CfnSchedule.TargetProperty(
                arn=cluster.cluster_arn,
                role_arn=run_role.role_arn,
                ecs_parameters=scheduler.CfnSchedule.EcsParametersProperty(
                    task_definition_arn=task.task_definition_arn,
                    launch_type="FARGATE",
                    platform_version="LATEST",
                    task_count=1,
                    network_configuration=scheduler.CfnSchedule.NetworkConfigurationProperty(
                        awsvpc_configuration=scheduler.CfnSchedule.AwsVpcConfigurationProperty(
                            subnets=subnets,
                            security_groups=[self.network.app_sg.security_group_id],
                            assign_public_ip="DISABLED",
                        )
                    ),
                ),
                retry_policy=scheduler.CfnSchedule.RetryPolicyProperty(
                    maximum_retry_attempts=2, maximum_event_age_in_seconds=3600
                ),
            ),
        )

        # Non-zero exit, or the task never started (e.g. a missing parameter)
        events.Rule(
            self,
            "IngestFailed",
            event_pattern=events.EventPattern(
                source=["aws.ecs"],
                detail_type=["ECS Task State Change"],
                detail={
                    "clusterArn": [cluster.cluster_arn],
                    "group": [f"family:{family}"],
                    "lastStatus": ["STOPPED"],
                    "$or": [
                        {"containers": {"exitCode": [{"anything-but": 0}]}},
                        {"stopCode": ["TaskFailedToStart"]},
                    ],
                },
            ),
            targets=[targets.SnsTopic(self.platform.alerts)],
        )
        return family

    def _db_bootstrap(self) -> str:
        """One-off task: creates this environment's database and users and
        writes DATABASE_URL / DATABASE_URL_INGEST to SSM (db_bootstrap.py).
        Run it once before giving the API or ingest any tasks."""
        role = iam.Role(
            self,
            "DbBootstrapRole",
            role_name=f"{self.prefix}-db-bootstrap",
            assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
        )
        role.add_to_policy(
            iam.PolicyStatement(
                actions=["ssm:GetParameter", "ssm:PutParameter"],
                resources=[
                    f"arn:aws:ssm:{Aws.REGION}:{Aws.ACCOUNT_ID}:parameter"
                    + param_name(self.env_name, name)
                    for name in ("DATABASE_URL", "DATABASE_URL_INGEST")
                ],
            )
        )
        task = self._task_definition("DbBootstrap", cpu=256, memory=512, task_role=role)
        db = self.data.db
        task.add_container(
            "db-bootstrap",
            image=self.image,
            command=["python", "db_bootstrap.py"],
            environment={
                "ENVIRONMENT": self.env_name,
                "DB_HOST": db.db_instance_endpoint_address,
                "DB_PORT": db.db_instance_endpoint_port,
                "PARAM_PREFIX": param_name(self.env_name, "").rstrip("/"),
                "AWS_REGION": Aws.REGION,
            },
            secrets={"DB_ADMIN_SECRET": ecs.Secret.from_secrets_manager(db.secret)},
            logging=ecs.LogDrivers.aws_logs(
                stream_prefix="db-bootstrap", log_group=self._log_group("DbBootstrap")
            ),
        )
        return task.family

    def _ui(self, settings: Settings) -> None:
        """datagpt-ui moved as is. Its image comes from context because its
        source isn't in this repo."""
        port = settings.datagpt_ui_port
        ui_role = iam.Role(
            self,
            "UiTaskRole",
            assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
        )
        # The UI image is built elsewhere and runs on x86 today
        task = self._task_definition(
            "Ui",
            cpu=256,
            memory=512,
            task_role=ui_role,
            arch=ecs.CpuArchitecture.X86_64,
        )
        task.add_container(
            "ui",
            image=ecs.ContainerImage.from_registry(settings.datagpt_ui_image),
            port_mappings=[ecs.PortMapping(container_port=port)],
            logging=ecs.LogDrivers.aws_logs(
                stream_prefix="ui", log_group=self._log_group("Ui")
            ),
        )
        service = ecs.FargateService(
            self,
            "UiService",
            service_name=f"{self.prefix}-datagpt-ui",
            cluster=self.platform.cluster,
            task_definition=task,
            desired_count=1,
            security_groups=[self.network.app_sg],
            vpc_subnets=ec2.SubnetSelection(
                subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS
            ),
            min_healthy_percent=100,
            max_healthy_percent=200,
            circuit_breaker=ecs.DeploymentCircuitBreaker(enable=True, rollback=True),
        )
        tg = elbv2.ApplicationTargetGroup(
            self,
            "UiTg",
            vpc=self.network.vpc,
            port=port,
            protocol=elbv2.ApplicationProtocol.HTTP,
            target_type=elbv2.TargetType.IP,
            health_check=elbv2.HealthCheck(path="/", healthy_http_codes="200-399"),
        )
        service.attach_to_application_target_group(tg)
        elbv2.ApplicationListenerRule(
            self,
            "UiRule",
            listener=self.platform.ui_listener,
            priority=100,
            conditions=[elbv2.ListenerCondition.path_patterns(["/*"])],
            action=elbv2.ListenerAction.forward([tg]),
        )

    def _alarms(self, service: ecs.FargateService) -> None:
        action = cw_actions.SnsAction(self.platform.alerts)
        alarms = [
            cloudwatch.Alarm(
                self,
                "ApiUnhealthy",
                alarm_name=f"{self.prefix}-api-unhealthy-targets",
                metric=self.api_tg.metrics.unhealthy_host_count(
                    period=Duration.minutes(1), statistic="Maximum"
                ),
                threshold=1,
                evaluation_periods=3,
                comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
                treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
            ),
            cloudwatch.Alarm(
                self,
                "Api5xx",
                alarm_name=f"{self.prefix}-api-5xx",
                metric=self.api_tg.metrics.http_code_target(
                    elbv2.HttpCodeTarget.TARGET_5XX_COUNT, period=Duration.minutes(5)
                ),
                threshold=10,
                evaluation_periods=1,
                treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
            ),
            cloudwatch.Alarm(
                self,
                "ApiCpu",
                alarm_name=f"{self.prefix}-api-cpu",
                metric=service.metric_cpu_utilization(period=Duration.minutes(5)),
                threshold=85,
                evaluation_periods=3,
            ),
        ]
        for alarm in alarms:
            alarm.add_alarm_action(action)
            alarm.add_ok_action(action)
