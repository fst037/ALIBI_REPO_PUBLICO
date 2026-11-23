"""Backstop for the models' endpoint scale-to-zero.

Application Auto Scaling is what normally takes the inference component to 0
copies, and managed instance scaling then releases the GPU. This function does
not replace either: it checks, every few minutes, that they actually happened,
and finishes the job when they did not. Nobody should be billed ~$1.5/hr for a
GPU that nothing has used.

1. Idle = no invocations of the component for IDLE_MINUTES, and it was not
   created or changed in that window either (a fresh deploy or a wake gets a
   full window). Idle with copies still up: set the copies to 0.
2. Still idle at HARD_IDLE_MINUTES with an instance still running — copies that
   would not go down, or an endpoint stuck in Updating that refuses changes:
   delete the endpoint (when DELETE_STUCK_ENDPOINT is true). Re-creating it is
   one ml-deploy.yml run; leaving it up costs money every hour.

Missing metric data counts as no invocations (an endpoint nobody calls emits
none), which is why the "was it changed recently" check guards the window.
"""

import datetime
import os

import boto3
from botocore.exceptions import ClientError

ENDPOINT_NAME = os.environ["ENDPOINT_NAME"]
IC_NAME = os.environ["IC_NAME"]
IDLE_MINUTES = int(os.environ["IDLE_MINUTES"])
HARD_IDLE_MINUTES = int(os.environ["HARD_IDLE_MINUTES"])
DELETE_STUCK = os.environ.get("DELETE_STUCK_ENDPOINT", "true").lower() == "true"

sagemaker = boto3.client("sagemaker")
cloudwatch = boto3.client("cloudwatch")


def handler(_event, _context):
    now = datetime.datetime.now(datetime.timezone.utc)

    try:
        endpoint = sagemaker.describe_endpoint(EndpointName=ENDPOINT_NAME)
    except ClientError as exc:
        if "Could not find" in str(exc):
            return {"action": "none", "reason": "no endpoint"}
        raise
    instances = int(endpoint["ProductionVariants"][0].get("CurrentInstanceCount", 0))
    if instances == 0:
        return {"action": "none", "reason": "no instance running"}

    desired, changed = 0, endpoint["CreationTime"]
    try:
        ic = sagemaker.describe_inference_component(InferenceComponentName=IC_NAME)
        runtime = ic.get("RuntimeConfig", {})
        desired = int(runtime.get("DesiredCopyCount", 0))
        changed = max(ic["CreationTime"], ic["LastModifiedTime"], changed)
        print(f"copies={runtime.get('CurrentCopyCount', 0)}/{desired}")
    except ClientError as exc:
        print(f"could not describe {IC_NAME}: {exc}")

    quiet_since = max(changed, _last_invocation(now) or changed)
    idle_for = (now - quiet_since).total_seconds() / 60
    print(f"instances={instances} idle_for={idle_for:.0f}min")

    if idle_for < IDLE_MINUTES:
        return {"action": "none", "reason": "recently used or changed", "idle_minutes": idle_for}

    if desired >= 1:
        try:
            sagemaker.update_inference_component_runtime_config(
                InferenceComponentName=IC_NAME, DesiredRuntimeConfig={"CopyCount": 0}
            )
            print(f"idle {idle_for:.0f} min: asked {IC_NAME} for 0 copies")
        except ClientError as exc:
            print(f"scale-down of {IC_NAME} refused: {exc}")

    if idle_for >= HARD_IDLE_MINUTES and DELETE_STUCK:
        try:
            sagemaker.delete_endpoint(EndpointName=ENDPOINT_NAME)
            print(f"idle {idle_for:.0f} min with an instance still up: deleted {ENDPOINT_NAME}")
            return {"action": "deleted_endpoint", "idle_minutes": idle_for}
        except ClientError as exc:
            print(f"could not delete {ENDPOINT_NAME}: {exc}")
            return {"action": "delete_failed", "error": str(exc)}

    return {"action": "scaled_down" if desired >= 1 else "waiting", "idle_minutes": idle_for}


def _last_invocation(now: datetime.datetime) -> datetime.datetime | None:
    """Most recent minute with an invocation of the component, within HARD_IDLE_MINUTES."""
    start = now - datetime.timedelta(minutes=HARD_IDLE_MINUTES + 5)
    latest = None
    paginator = cloudwatch.get_paginator("list_metrics")
    for page in paginator.paginate(
        Namespace="AWS/SageMaker",
        MetricName="Invocations",
        Dimensions=[{"Name": "InferenceComponentName", "Value": IC_NAME}],
    ):
        for metric in page["Metrics"]:
            data = cloudwatch.get_metric_statistics(
                Namespace="AWS/SageMaker",
                MetricName="Invocations",
                Dimensions=metric["Dimensions"],
                StartTime=start,
                EndTime=now,
                Period=60,
                Statistics=["Sum"],
            )
            for point in data["Datapoints"]:
                if point["Sum"] > 0 and (latest is None or point["Timestamp"] > latest):
                    latest = point["Timestamp"]
    return latest
