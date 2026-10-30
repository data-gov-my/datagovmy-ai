from aws_cdk import aws_wafv2 as wafv2
from constructs import Construct

from .config import PROJECT, Settings


class ApiWebAcl(Construct):
    """Regional WAF for whichever ALB fronts the API (Singapore today, the
    Malaysia ALB after the DNS move). A web ACL can only attach to an ALB in
    its own region.

    `wafMode` COUNT (the default) makes every rule, including the managed
    rule set, count without blocking, so attaching it changes nothing for
    users. Check the metrics, then switch to BLOCK. The managed rules in
    particular can match code in chat questions (e.g. `<script>`).
    """

    def __init__(
        self, scope: Construct, id: str, *, settings: Settings, alb_arn: str
    ) -> None:
        super().__init__(scope, id)
        block = settings.waf_mode == "BLOCK"

        def visibility(name: str):
            return wafv2.CfnWebACL.VisibilityConfigProperty(
                cloud_watch_metrics_enabled=True,
                metric_name=name,
                sampled_requests_enabled=True,
            )

        rate_action = wafv2.CfnWebACL.RuleActionProperty(
            **({"block": {}} if block else {"count": {}})
        )

        def rate_rule(name: str, priority: int, path: str, limit: int):
            """Per-IP limit on one path prefix. The browser calls the API
            directly (NEXT_PUBLIC_AI_URL), so the IP is the user's own."""
            return wafv2.CfnWebACL.RuleProperty(
                name=name,
                priority=priority,
                action=rate_action,
                statement=wafv2.CfnWebACL.StatementProperty(
                    rate_based_statement=wafv2.CfnWebACL.RateBasedStatementProperty(
                        limit=limit,
                        aggregate_key_type="IP",
                        scope_down_statement=wafv2.CfnWebACL.StatementProperty(
                            byte_match_statement=wafv2.CfnWebACL.ByteMatchStatementProperty(
                                field_to_match=wafv2.CfnWebACL.FieldToMatchProperty(
                                    uri_path={}
                                ),
                                positional_constraint="STARTS_WITH",
                                search_string=path,
                                text_transformations=[
                                    wafv2.CfnWebACL.TextTransformationProperty(
                                        priority=0, type="LOWERCASE"
                                    )
                                ],
                            )
                        ),
                    )
                ),
                visibility_config=visibility(name),
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
                    override_action=wafv2.CfnWebACL.OverrideActionProperty(
                        **({"none": {}} if block else {"count": {}})
                    ),
                    statement=wafv2.CfnWebACL.StatementProperty(
                        managed_rule_group_statement=wafv2.CfnWebACL.ManagedRuleGroupStatementProperty(
                            vendor_name="AWS",
                            name="AWSManagedRulesCommonRuleSet",
                            # Chat history and /generate-meta payloads exceed the
                            # rule's 8 KB body limit; always count, never block
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
                rate_rule("chat-rate", 1, "/chat", settings.waf_chat_rate_limit),
                # /generate-meta has no auth and every call is a model call
                rate_rule(
                    "generate-meta-rate",
                    2,
                    "/generate-meta",
                    settings.waf_generate_meta_rate_limit,
                ),
            ],
        )
        wafv2.CfnWebACLAssociation(
            self, "Association", resource_arn=alb_arn, web_acl_arn=acl.attr_arn
        )
