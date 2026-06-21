import os
from datetime import datetime, timedelta, timezone

import boto3
from aws_lambda_powertools import Logger
from botocore.exceptions import ClientError

logger = Logger(service="ml-cost-optimizer")

REGION = os.environ.get("AWS_REGION", "eu-west-1")
SNS_TOPIC_ARN = os.environ.get("SNS_TOPIC_ARN")

HIGH_THRESHOLD = float(os.environ.get("HIGH_THRESHOLD", "50"))
IDLE_COST_THRESHOLD = float(os.environ.get("IDLE_COST_THRESHOLD", "10"))

sm = boto3.client("sagemaker", region_name=REGION)
cw = boto3.client("cloudwatch", region_name=REGION)
sns = boto3.client("sns", region_name=REGION)


# ── AWS helpers ───────────────────────────────────────────────────────────────


def get_tags(resource_arn: str) -> dict:
    try:
        raw = sm.list_tags(ResourceArn=resource_arn).get("Tags", [])
        return {t["Key"]: t["Value"] for t in raw}
    except ClientError:
        return {}


def get_cost(resource: dict) -> float:
    """Estimation mensuelle basée sur le type d'instance (Pricing API fallback)."""
    hourly_prices = {
        "ml.t3.medium": 0.05,
        "ml.t3.xlarge": 0.20,
        "ml.m5.xlarge": 0.23,
        "ml.p3.2xlarge": 3.83,
    }
    hourly = hourly_prices.get(resource.get("instance_type", ""), 0.10)
    return round(hourly * 730, 2)


def is_idle(resource: dict) -> bool:
    name = resource["name"]
    kind = resource["kind"]
    end = datetime.now(timezone.utc)
    start = end - timedelta(hours=24)

    try:
        if kind == "notebook":
            resp = cw.get_metric_statistics(
                Namespace="AWS/SageMaker",
                MetricName="CPUUtilization",
                Dimensions=[{"Name": "NotebookInstanceName", "Value": name}],
                StartTime=start,
                EndTime=end,
                Period=3600,
                Statistics=["Average"],
            )
            points = resp.get("Datapoints", [])
            if not points:
                return True
            return (sum(d["Average"] for d in points) / len(points)) < 5.0

        if kind == "endpoint":
            resp = cw.get_metric_statistics(
                Namespace="AWS/SageMaker",
                MetricName="Invocations",
                Dimensions=[{"Name": "EndpointName", "Value": name}],
                StartTime=start,
                EndTime=end,
                Period=3600,
                Statistics=["Sum"],
            )
            return sum(d["Sum"] for d in resp.get("Datapoints", [])) == 0

    except ClientError:
        pass

    return False


def stop_resource(resource: dict) -> None:
    try:
        if resource["kind"] == "notebook":
            sm.stop_notebook_instance(NotebookInstanceName=resource["name"])
            logger.info(f"[AUTO-STOP] {resource['name']}")
    except ClientError as e:
        logger.error(f"Failed to stop {resource['name']}: {e}")


def notify_only(resource: dict, cost: float, reason: str) -> None:
    notify(
        f"[ML Cost] Alert — {resource['name']}",
        f"{resource['kind'].title()} '{resource['name']}' is idle (cost: ${cost}/mo) — no action taken ({reason}).",
    )


def alert_escalation(resource: dict) -> None:
    notify(
        f"[ML Cost] ⚠️ High cost prod resource — {resource['name']}",
        f"{resource['kind'].title()} '{resource['name']}' is in prod and exceeds cost threshold (>${HIGH_THRESHOLD}/mo).",
    )


def notify_slack(resource: dict, action: str) -> None:
    notify(
        f"[ML Cost] {resource['name']} — {action}",
        f"{resource['kind'].title()} '{resource['name']}': {action}.",
    )


def notify(subject: str, message: str) -> None:
    if not SNS_TOPIC_ARN:
        logger.warning("SNS_TOPIC_ARN not set, skipping notification")
        return
    try:
        sns.publish(TopicArn=SNS_TOPIC_ARN, Subject=subject, Message=message)
        logger.info(f"SNS sent: {subject}")
    except ClientError as e:
        logger.warning(f"SNS failed: {e}")


# ── Core logic ────────────────────────────────────────────────────────────────


def handle_resource(resource: dict) -> None:
    tags = resource["tags"]
    cost = get_cost(resource)
    idle = is_idle(resource)

    # 1. HIGH CRITICALITY → NO ACTION
    if tags.get("DataCriticality") == "high":
        notify_only(resource, cost, reason="critical data")
        return

    # 2. PROD → NO AUTO STOP
    if tags.get("Environment") == "prod":
        if cost > HIGH_THRESHOLD:
            alert_escalation(resource)
        return

    # 3. SAFE AUTO STOP (DEV ONLY)
    if tags.get("AutoStop") == "true" and idle and cost > IDLE_COST_THRESHOLD:
        stop_resource(resource)
        notify_slack(resource, action="stopped")
        return

    # 4. DEFAULT → alert only
    notify_slack(resource, action="monitoring only")


# ── Handler ───────────────────────────────────────────────────────────────────


def handler(event, context):
    logger.info("Guardrail starting")

    # --- Notebooks ---
    try:
        for page in sm.get_paginator("list_notebook_instances").paginate():
            for nb in page["NotebookInstances"]:
                if nb["NotebookInstanceStatus"] != "InService":
                    continue
                arn = nb["NotebookInstanceArn"]
                handle_resource(
                    {
                        "name": nb["NotebookInstanceName"],
                        "kind": "notebook",
                        "arn": arn,
                        "instance_type": nb.get("InstanceType", ""),
                        "tags": get_tags(arn),
                    }
                )
    except ClientError as e:
        logger.error(f"Error listing notebooks: {e}")

    # --- Endpoints ---
    try:
        for page in sm.get_paginator("list_endpoints").paginate():
            for ep in page["Endpoints"]:
                if ep["EndpointStatus"] != "InService":
                    continue
                arn = ep["EndpointArn"]
                handle_resource(
                    {
                        "name": ep["EndpointName"],
                        "kind": "endpoint",
                        "arn": arn,
                        "instance_type": "",
                        "tags": get_tags(arn),
                    }
                )
    except ClientError as e:
        logger.error(f"Error listing endpoints: {e}")

    logger.info("Guardrail done")
    return {"statusCode": 200}
