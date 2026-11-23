"""Stop the RDS instance when nobody has used it for a while; start it on demand.

One function, two callers:

- EventBridge, every few minutes, with {"action": "stop_if_idle"}.
- The backend API Lambda (app/db/waker.py), or a developer's
  `aws lambda invoke`, with anything else — an empty payload means "start".
  Starting is the safe default: a stray invoke can only cost a few hours of
  uptime, never take the database away from someone using it.

"Idle" means ALL of these held for the whole window:

- the backend API and the ingest committer Lambdas were not invoked. The
  backend opens one short-lived connection per request (NullPool), which
  RDS's once-a-minute DatabaseConnections gauge mostly misses, so the
  invocations are the real signal for the deployed app;
- DatabaseConnections stayed at or below the baseline (catches a SQL client
  or a migration holding a session open);
- the bastion is not running (a developer has, or recently had, a tunnel —
  the bastion stops itself once that ends).

Missing metric data counts as activity, so a freshly started instance always
gets a full window before it can be stopped again — same idea as the
bastion's idle clock restarting on boot.
"""

import datetime
import os

import boto3
from botocore.exceptions import ClientError

DB_INSTANCE_ID = os.environ["DB_INSTANCE_ID"]
IDLE_MINUTES = int(os.environ["IDLE_MINUTES"])
IDLE_MAX_CONNECTIONS = float(os.environ.get("IDLE_MAX_CONNECTIONS", "0"))
ACTIVITY_FUNCTIONS = [f for f in os.environ.get("ACTIVITY_FUNCTIONS", "").split(",") if f]
BASTION_INSTANCE_ID = os.environ.get("BASTION_INSTANCE_ID", "")

PERIOD = 300

rds = boto3.client("rds")
cloudwatch = boto3.client("cloudwatch")
ec2 = boto3.client("ec2")


def handler(event, _context):
    action = (event or {}).get("action", "start")
    status = _status()
    if action == "stop_if_idle":
        return _stop_if_idle(status)
    if status == "stopped":
        try:
            rds.start_db_instance(DBInstanceIdentifier=DB_INSTANCE_ID)
        except ClientError as exc:
            # AWS has no capacity for the instance class in this AZ right now.
            # Report it instead of crashing so callers can say so; the next
            # request retries the start.
            if exc.response["Error"]["Code"] == "InsufficientDBInstanceCapacity":
                print(f"no capacity to start {DB_INSTANCE_ID}: {exc}")
                return {"status": "stopped", "error": "no_capacity", "message": str(exc)}
            raise
        return {"status": "starting"}
    return {"status": status}


def _status():
    instances = rds.describe_db_instances(DBInstanceIdentifier=DB_INSTANCE_ID)["DBInstances"]
    return instances[0]["DBInstanceStatus"]


def _stop_if_idle(status):
    if status != "available":
        return {"status": status, "stopped": False, "reason": "not available"}
    busy = _busy_reason()
    if busy:
        return {"status": status, "stopped": False, "reason": busy}
    try:
        rds.stop_db_instance(DBInstanceIdentifier=DB_INSTANCE_ID)
    except ClientError as exc:
        return {"status": status, "stopped": False, "reason": str(exc)}
    print(f"stopped {DB_INSTANCE_ID}: idle for {IDLE_MINUTES} min")
    return {"status": "stopping", "stopped": True}


def _busy_reason():
    if BASTION_INSTANCE_ID and _bastion_running():
        return "bastion running"

    end = datetime.datetime.now(datetime.timezone.utc)
    start = end - datetime.timedelta(minutes=IDLE_MINUTES)

    for fn in ACTIVITY_FUNCTIONS:
        points = _datapoints("AWS/Lambda", "Invocations", "FunctionName", fn, "Sum", start, end)
        if any(p["Sum"] > 0 for p in points):
            return f"{fn} invoked"

    points = _datapoints(
        "AWS/RDS", "DatabaseConnections", "DBInstanceIdentifier", DB_INSTANCE_ID, "Maximum", start, end
    )
    # The newest period is often not published yet, hence the slack of two.
    if len(points) < IDLE_MINUTES * 60 // PERIOD - 2:
        return "not enough metric history (recently started?)"
    if any(p["Maximum"] > IDLE_MAX_CONNECTIONS for p in points):
        return "open connections"
    return None


def _datapoints(namespace, metric, dimension, value, statistic, start, end):
    return cloudwatch.get_metric_statistics(
        Namespace=namespace,
        MetricName=metric,
        Dimensions=[{"Name": dimension, "Value": value}],
        StartTime=start,
        EndTime=end,
        Period=PERIOD,
        Statistics=[statistic],
    )["Datapoints"]


def _bastion_running():
    reservations = ec2.describe_instances(InstanceIds=[BASTION_INSTANCE_ID])["Reservations"]
    state = reservations[0]["Instances"][0]["State"]["Name"]
    return state in ("pending", "running", "stopping")
