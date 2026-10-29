from aws_cdk import Aws, CfnOutput, Stack, aws_iam as iam
from constructs import Construct

from .config import CDK_QUALIFIER, PROJECT, Settings

GITHUB_OIDC = "token.actions.githubusercontent.com"


class CiStack(Stack):
    """GitHub Actions roles, assumed via OIDC (no static keys).

    One role per GitHub environment. The trust policy only accepts jobs that
    run in that environment, so the environment's protection rules (branch
    restriction, required reviewers) decide who can deploy.

    Both roles deploy through the CDK bootstrap roles, which can change any
    stack in the account/region: protect both environments accordingly.
    """

    def __init__(
        self, scope: Construct, id: str, *, settings: Settings, **kwargs
    ) -> None:
        super().__init__(scope, id, **kwargs)

        if settings.create_github_oidc_provider:
            provider_arn = iam.CfnOIDCProvider(
                self,
                "GithubOidc",
                url=f"https://{GITHUB_OIDC}",
                client_id_list=["sts.amazonaws.com"],
            ).attr_arn
        else:
            provider_arn = f"arn:aws:iam::{Aws.ACCOUNT_ID}:oidc-provider/{GITHUB_OIDC}"

        def github_role(name: str, gh_env: str) -> iam.Role:
            role = iam.Role(
                self,
                f"GithubRole{name.capitalize()}",
                role_name=f"{PROJECT}-gha-{name}",
                assumed_by=iam.WebIdentityPrincipal(
                    provider_arn,
                    conditions={
                        "StringEquals": {
                            f"{GITHUB_OIDC}:aud": "sts.amazonaws.com",
                            f"{GITHUB_OIDC}:sub": f"repo:{settings.github_repo}:environment:{gh_env}",
                        }
                    },
                ),
            )
            CfnOutput(self, f"GithubRoleArn{name.capitalize()}", value=role.role_arn)
            return role

        for env in ("staging", "prod"):
            role = github_role(env, "production" if env == "prod" else "staging")
            role.add_to_policy(
                iam.PolicyStatement(
                    actions=["sts:AssumeRole"],
                    resources=[
                        f"arn:aws:iam::{Aws.ACCOUNT_ID}:role/cdk-{CDK_QUALIFIER}-*-{Aws.ACCOUNT_ID}-{Aws.REGION}"
                    ],
                )
            )
            role.add_to_policy(
                iam.PolicyStatement(
                    actions=["cloudformation:DescribeStacks"], resources=["*"]
                )
            )
            if env == "staging":
                # Images are built once, in the staging job, and promoted to prod
                role.add_to_policy(
                    iam.PolicyStatement(
                        actions=["ecr:GetAuthorizationToken"], resources=["*"]
                    )
                )
                role.add_to_policy(
                    iam.PolicyStatement(
                        actions=[
                            "ecr:BatchCheckLayerAvailability",
                            "ecr:BatchGetImage",
                            "ecr:CompleteLayerUpload",
                            "ecr:DescribeImages",
                            "ecr:InitiateLayerUpload",
                            "ecr:PutImage",
                            "ecr:UploadLayerPart",
                        ],
                        resources=[
                            f"arn:aws:ecr:{Aws.REGION}:{Aws.ACCOUNT_ID}:repository/{PROJECT}"
                        ],
                    )
                )

        # Docs-triggered and manual ingest runs. Unattended, so it has its own
        # environment (no reviewers) and can only start the ingest tasks.
        ingest = github_role("ingest", "ingest")
        ingest.add_to_policy(
            iam.PolicyStatement(
                actions=["cloudformation:DescribeStacks"],
                resources=[
                    f"arn:aws:cloudformation:{Aws.REGION}:{Aws.ACCOUNT_ID}:stack/DatagovmyAi-Service-*/*"
                ],
            )
        )
        ingest.add_to_policy(
            iam.PolicyStatement(
                actions=["ecs:RunTask"],
                resources=[
                    f"arn:aws:ecs:{Aws.REGION}:{Aws.ACCOUNT_ID}:task-definition/{PROJECT}-{env}-ingest:*"
                    for env in ("staging", "prod")
                ],
            )
        )
        ingest.add_to_policy(
            iam.PolicyStatement(
                actions=["ecs:DescribeTasks"],
                resources=[
                    f"arn:aws:ecs:{Aws.REGION}:{Aws.ACCOUNT_ID}:task/{PROJECT}/*"
                ],
            )
        )
        ingest.add_to_policy(
            iam.PolicyStatement(
                actions=["iam:PassRole"],
                resources=[
                    f"arn:aws:iam::{Aws.ACCOUNT_ID}:role/{PROJECT}-{env}-{kind}"
                    for env in ("staging", "prod")
                    for kind in ("task", "exec")
                ],
            )
        )
