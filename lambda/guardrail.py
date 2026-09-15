"""SagePulse guardrail Lambda.

Runs on a schedule, lists in-service SageMaker notebooks and endpoints, estimates
their monthly cost from the instance type, checks CloudWatch for activity, and
applies a tag-based policy:

  DataCriticality=high                → notify only, never touch
  Environment=prod                    → escalate if estimated cost > HIGH_THRESHOLD, else silent
  AutoStop=true + idle + cost > IDLE_COST_THRESHOLD
      notebook                        → stop it (unless DRY_RUN) + notify
      endpoint                        → notify "idle endpoint" (endpoints are never deleted automatically)
  anything else                       → log only (no notification: avoids one email per resource every run)

DRY_RUN=true (the Terraform default) logs every decision and performs no stop.
"""

import os
from datetime import datetime, timedelta, timezone

import boto3
from aws_lambda_powertools import Logger
from botocore.exceptions import ClientError

logger = Logger(service="sagepulse")

REGION = os.environ.get("AWS_REGION", "eu-west-1")
SNS_TOPIC_ARN = os.environ.get("SNS_TOPIC_ARN")
DRY_RUN = os.environ.get("DRY_RUN", "true").lower() == "true"

HIGH_THRESHOLD = float(os.environ.get("HIGH_THRESHOLD", "50"))
IDLE_COST_THRESHOLD = float(os.environ.get("IDLE_COST_THRESHOLD", "10"))
IDLE_WINDOW_HOURS = int(os.environ.get("IDLE_WINDOW_HOURS", "24"))
NOTEBOOK_IDLE_CPU_PCT = float(os.environ.get("NOTEBOOK_IDLE_CPU_PCT", "5"))

HOURS_PER_MONTH = 730

# On-demand eu-west-1 prices (USD/hour), rounded — an estimate, not a bill.
# Unknown types fall back to FALLBACK_HOURLY and are flagged in the notification.
HOURLY_PRICES = {
    "ml.t2.medium": 0.05,
    "ml.t3.medium": 0.05,
    "ml.t3.large": 0.10,
    "ml.t3.xlarge": 0.20,
    "ml.t3.2xlarge": 0.40,
    "ml.m5.large": 0.13,
    "ml.m5.xlarge": 0.25,
    "ml.m5.2xlarge": 0.51,
    "ml.m5.4xlarge": 1.02,
    "ml.c5.xlarge": 0.23,
    "ml.c5.2xlarge": 0.45,
    "ml.g4dn.xlarge": 0.82,
    "ml.g4dn.2xlarge": 1.17,
    "ml.p3.2xlarge": 4.13,
    "ml.p3.8xlarge": 15.90,
}
FALLBACK_HOURLY = 0.10

sm = boto3.client("sagemaker", region_name=REGION)
cw = boto3.client("cloudwatch", region_name=REGION)
sns = boto3.client("sns", region_name=REGION)


# ── AWS helpers ───────────────────────────────────────────────────────────────


def get_tags(resource_arn: str) -> dict:
    try:
        raw = sm.list_tags(ResourceArn=resource_arn).get("Tags", [])
        return {t["Key"]: t["Value"] for t in raw}
    except ClientError as e:
        logger.warning(f"list_tags failed for {resource_arn}: {e}")
        return {}


def get_endpoint_instances(endpoint_name: str) -> list[tuple[str, int]]:
    """Return [(instance_type, count)] for every production variant of an endpoint.

    Serverless variants have no instance type and are returned as ("serverless", 0).
    """
    try:
        cfg_name = sm.describe_endpoint(EndpointName=endpoint_name)[
            "EndpointConfigName"
        ]
        cfg = sm.describe_endpoint_config(EndpointConfigName=cfg_name)
    except ClientError as e:
        logger.warning(f"describe endpoint {endpoint_name} failed: {e}")
        return []
    out = []
    for variant in cfg.get("ProductionVariants", []):
        if "ServerlessConfig" in variant:
            out.append(("serverless", 0))
        else:
            out.append(
                (
                    variant.get("InstanceType", ""),
                    int(variant.get("InitialInstanceCount", 1)),
                )
            )
    return out


def estimate_monthly_cost(resource: dict) -> tuple[float, bool]:
    """(estimated USD/month, is_estimate_reliable).

    Notebooks: one instance. Endpoints: sum over variants of type × count.
    Reliable is False when any instance type is unknown to HOURLY_PRICES.
    """
    instances = resource.get("instances") or [(resource.get("instance_type", ""), 1)]
    total, reliable = 0.0, True
    for itype, count in instances:
        if itype == "serverless":
            continue  # billed per invocation; idle serverless endpoints cost ~0
        hourly = HOURLY_PRICES.get(itype)
        if hourly is None:
            hourly, reliable = FALLBACK_HOURLY, False
        total += hourly * count * HOURS_PER_MONTH
    return round(total, 2), reliable


def is_idle(resource: dict) -> bool:
    """Notebook: average CPU below NOTEBOOK_IDLE_CPU_PCT over the window (no datapoints counts as idle).

    Endpoint: zero invocations over the window.
    Any CloudWatch error → not idle (fail safe: never stop on missing evidence).
    """
    name = resource["name"]
    kind = resource["kind"]
    end = datetime.now(timezone.utc)
    start = end - timedelta(hours=IDLE_WINDOW_HOURS)

    try:
        if kind == "notebook":
            resp = cw.get_metric_statistics(
                Namespace="/aws/sagemaker/NotebookInstances",
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
            return (
                sum(d["Average"] for d in points) / len(points)
            ) < NOTEBOOK_IDLE_CPU_PCT

        if kind == "endpoint":
            resp = cw.get_metric_statistics(
                Namespace="AWS/SageMaker",
                MetricName="Invocations",
                Dimensions=[
                    {"Name": "EndpointName", "Value": name},
                    {"Name": "VariantName", "Value": "AllTraffic"},
                ],
                StartTime=start,
                EndTime=end,
                Period=3600,
                Statistics=["Sum"],
            )
            return sum(d["Sum"] for d in resp.get("Datapoints", [])) == 0

    except ClientError as e:
        logger.warning(f"CloudWatch query failed for {name}: {e}")

    return False


def stop_notebook(resource: dict) -> bool:
    if DRY_RUN:
        logger.info(f"[DRY_RUN] would stop notebook {resource['name']}")
        return False
    try:
        sm.stop_notebook_instance(NotebookInstanceName=resource["name"])
        logger.info(f"[AUTO-STOP] {resource['name']}")
        return True
    except ClientError as e:
        logger.error(f"Failed to stop {resource['name']}: {e}")
        return False


def notify(subject: str, message: str) -> None:
    if not SNS_TOPIC_ARN:
        logger.warning("SNS_TOPIC_ARN not set, skipping notification")
        return
    if DRY_RUN:
        subject = f"[DRY_RUN] {subject}"
    try:
        sns.publish(TopicArn=SNS_TOPIC_ARN, Subject=subject[:100], Message=message)
        logger.info(f"SNS sent: {subject}")
    except ClientError as e:
        logger.warning(f"SNS failed: {e}")


def _describe(resource: dict, cost: float, reliable: bool) -> str:
    note = "" if reliable else " (unknown instance type, fallback price used)"
    return f"{resource['kind'].title()} '{resource['name']}' — estimated ${cost}/month{note}"


# ── Policy ────────────────────────────────────────────────────────────────────


def handle_resource(resource: dict) -> str:
    """Apply the policy to one resource. Returns the decision taken (for logs and tests)."""
    tags = resource["tags"]
    cost, reliable = estimate_monthly_cost(resource)
    desc = _describe(resource, cost, reliable)

    # 1. Critical data → never touch, always tell someone
    if tags.get("DataCriticality") == "high":
        notify(
            f"[SagePulse] Critical resource review — {resource['name']}",
            f"{desc}. DataCriticality=high: no action taken.",
        )
        return "notify_critical"

    # 2. Prod → never auto-stop; escalate only above threshold
    if tags.get("Environment") == "prod":
        if cost > HIGH_THRESHOLD:
            notify(
                f"[SagePulse] Prod resource above threshold — {resource['name']}",
                f"{desc}, above HIGH_THRESHOLD=${HIGH_THRESHOLD}. Review whether this capacity is still needed.",
            )
            return "escalate_prod"
        return "prod_ok"

    # 3. Opt-in auto-stop for idle, non-trivial resources
    if (
        tags.get("AutoStop") == "true"
        and cost > IDLE_COST_THRESHOLD
        and is_idle(resource)
    ):
        if resource["kind"] == "notebook":
            stopped = stop_notebook(resource)
            verb = (
                "stopped"
                if stopped
                else "would be stopped (dry run)"
                if DRY_RUN
                else "could not be stopped"
            )
            notify(
                f"[SagePulse] Idle notebook {verb} — {resource['name']}",
                f"{desc}, idle for {IDLE_WINDOW_HOURS}h: {verb}.",
            )
            return "stopped" if stopped else "stop_skipped"
        notify(
            f"[SagePulse] Idle endpoint — {resource['name']}",
            f"{desc}, no invocations for {IDLE_WINDOW_HOURS}h. Endpoints are never deleted automatically: delete it or remove AutoStop.",
        )
        return "notify_idle_endpoint"

    # 4. Default → log only. No notification: one email per resource every run is noise.
    logger.info(f"[monitor] {desc} tags={tags}")
    return "monitor"


# ── Handler ───────────────────────────────────────────────────────────────────


def _notebooks():
    for page in sm.get_paginator("list_notebook_instances").paginate():
        for nb in page["NotebookInstances"]:
            if nb["NotebookInstanceStatus"] != "InService":
                continue
            arn = nb["NotebookInstanceArn"]
            yield {
                "name": nb["NotebookInstanceName"],
                "kind": "notebook",
                "arn": arn,
                "instance_type": nb.get("InstanceType", ""),
                "tags": get_tags(arn),
            }


def _endpoints():
    for page in sm.get_paginator("list_endpoints").paginate():
        for ep in page["Endpoints"]:
            if ep["EndpointStatus"] != "InService":
                continue
            arn = ep["EndpointArn"]
            yield {
                "name": ep["EndpointName"],
                "kind": "endpoint",
                "arn": arn,
                "instances": get_endpoint_instances(ep["EndpointName"]),
                "tags": get_tags(arn),
            }


def handler(event, context):
    logger.info(f"Guardrail starting (dry_run={DRY_RUN})")
    decisions: dict[str, int] = {}

    for lister in (_notebooks, _endpoints):
        try:
            for resource in lister():
                decision = handle_resource(resource)
                decisions[decision] = decisions.get(decision, 0) + 1
        except ClientError as e:
            logger.error(f"Listing failed in {lister.__name__}: {e}")

    logger.info(f"Guardrail done: {decisions}")
    return {"statusCode": 200, "dry_run": DRY_RUN, "decisions": decisions}
