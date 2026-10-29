"""Forward SNS alerts (CloudWatch alarms, ECS task failures) to Discord."""

import json
import os
import urllib.request

import boto3

_webhook = None


def _webhook_url() -> str:
    global _webhook
    if _webhook is None:
        _webhook = boto3.client("ssm").get_parameter(
            Name=os.environ["WEBHOOK_PARAM"], WithDecryption=True
        )["Parameter"]["Value"]
    return _webhook


def _format(message: str) -> str:
    try:
        body = json.loads(message)
    except json.JSONDecodeError:
        return message

    # CloudWatch alarm
    if "AlarmName" in body:
        return (
            f"**{body['AlarmName']}** is {body.get('NewStateValue')}\n"
            f"{body.get('NewStateReason', '')}"
        )

    # EventBridge: ECS task state change
    detail = body.get("detail", {})
    if body.get("detail-type") == "ECS Task State Change":
        exits = [
            f"{c.get('name')}={c.get('exitCode')}" for c in detail.get("containers", [])
        ]
        return (
            f"**ECS task failed** ({detail.get('group')})\n"
            f"stopCode: {detail.get('stopCode')}, exit: {', '.join(exits) or 'n/a'}\n"
            f"{detail.get('stoppedReason', '')}\n"
            f"task: {detail.get('taskArn')}"
        )

    return message


def handler(event, context):
    for record in event.get("Records", []):
        content = _format(record["Sns"]["Message"])[:1900]  # Discord limit: 2000
        req = urllib.request.Request(
            _webhook_url(),
            data=json.dumps({"content": content}).encode(),
            # Discord rejects urllib's default User-Agent
            headers={
                "Content-Type": "application/json",
                "User-Agent": "datagovmy-ai-alerts",
            },
        )
        urllib.request.urlopen(req, timeout=5)
