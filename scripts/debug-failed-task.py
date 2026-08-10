#!/usr/bin/env python3
"""
Inspect a CAMRIE pipeline by ID. Queries DynamoDB, Step Functions, Batch,
CloudWatch, and S3 — all via your AWS IAM profile.

Usage:
    python scripts/debug-failed-task.py -p 64730201-9642-4a66-bfc8-65fa93573e90
    python scripts/debug-failed-task.py -i 0                # most recent (needs query first)
    python scripts/debug-failed-task.py -i 0 --failed-only
    python scripts/debug-failed-task.py -p <id> --log-lines 200
    python scripts/debug-failed-task.py -p <id> --download-only
    python scripts/debug-failed-task.py -p <id> --run-local
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import boto3

SCRIPT_DIR        = Path(__file__).parent
REPO_ROOT         = SCRIPT_DIR.parent
FAILED_TASKS_FILE = SCRIPT_DIR / "debug-failed-tasks.json"
DEBUG_DIR         = Path(tempfile.gettempdir()) / "camrie-debug"

AWS_REGION        = "us-east-1"
PIPELINES_TABLE   = "CloudMR-Pipelines"
DATA_BUCKET       = "cloudmr-data-cloudmrhub-brain-us-east-1"
RESULTS_BUCKET    = "cloudmr-results-cloudmrhub-brain-us-east-1"
CW_LOG_GROUP_CPU  = "/ecs/camrie-Prod"
CW_LOG_GROUP_GPU  = "/ecs/camrie-gpu-Prod"


# ─── Helpers ────────────────────────────────────────────────────────────────────
def sep(title=""):
    if title:
        print(f"\n{'='*60}")
        print(f"  {title}")
        print(f"{'='*60}")
    else:
        print(f"{'─'*60}")


def fmt_ts(ts):
    if not ts:
        return "—"
    if isinstance(ts, datetime):
        return ts.strftime("%Y-%m-%d %H:%M:%S UTC")
    try:
        if isinstance(ts, (int, float)):
            return datetime.fromtimestamp(ts / 1000, tz=timezone.utc).strftime(
                "%Y-%m-%d %H:%M:%S UTC")
    except Exception:
        pass
    return str(ts)


def aws_session(profile, region=AWS_REGION):
    return boto3.Session(profile_name=profile, region_name=region)


# ─── DynamoDB: get pipeline record ──────────────────────────────────────────────
def get_pipeline_from_ddb(ddb, pipeline_id):
    """Fetch a pipeline record from CloudMR-Pipelines by ID."""
    resp = ddb.get_item(TableName=PIPELINES_TABLE,
                        Key={"pipeline": {"S": pipeline_id}})
    item = resp.get("Item")
    if not item:
        return None
    return {k: list(v.values())[0] for k, v in item.items()}


# ─── Load from JSON (for --index mode) ──────────────────────────────────────────
def load_tasks():
    if not FAILED_TASKS_FILE.exists():
        print(f"[ERROR] {FAILED_TASKS_FILE} not found.")
        print("  Run first: python scripts/debug-query-pipelines.py")
        sys.exit(1)
    with open(FAILED_TASKS_FILE) as f:
        return json.load(f)


def find_task_by_index(data, index, failed_only=False):
    pool = data.get("failed_tasks" if failed_only else "all_tasks", [])
    if not pool:
        pool = data.get("all_tasks", [])
    if 0 <= index < len(pool):
        return pool[index]
    print(f"[ERROR] Index {index} out of range (0..{len(pool)-1})")
    sys.exit(1)


# ─── Step Functions: find execution by pipeline ID ───────────────────────────────
def find_state_machines(sfn):
    """Find CAMRIE state machine ARNs."""
    arns = []
    for sm in sfn.list_state_machines()["stateMachines"]:
        name = sm["name"].lower()
        if "camrie" in name or "calculationstatemachine" in name.replace("-", ""):
            arns.append(sm["stateMachineArn"])
    return arns


def find_execution_for_pipeline(sfn, pipeline_id, created_at=None):
    """Search SFN executions that match this pipeline (by input content or timing)."""
    sm_arns = find_state_machines(sfn)
    if not sm_arns:
        return None

    for sm_arn in sm_arns:
        paginator = sfn.get_paginator("list_executions")
        for page in paginator.paginate(stateMachineArn=sm_arn, maxResults=100):
            for ex in page.get("executions", []):
                # Check execution name
                if pipeline_id in ex.get("name", ""):
                    return ex["executionArn"]

    # Fallback: check inputs (slower)
    for sm_arn in sm_arns:
        paginator = sfn.get_paginator("list_executions")
        for page in paginator.paginate(stateMachineArn=sm_arn, maxResults=50):
            for ex in page.get("executions", []):
                try:
                    desc = sfn.describe_execution(executionArn=ex["executionArn"])
                    if pipeline_id in desc.get("input", ""):
                        return ex["executionArn"]
                except Exception:
                    pass
    return None


def inspect_execution(sfn, execution_arn):
    """Describe SFN execution, return batch_job_id and task input."""
    try:
        desc = sfn.describe_execution(executionArn=execution_arn)
    except Exception as e:
        print(f"  [ERROR] {e}")
        return None, None

    print(f"  ARN    : {execution_arn}")
    print(f"  Status : {desc.get('status', '?')}")
    print(f"  Started: {fmt_ts(desc.get('startDate'))}")
    print(f"  Stopped: {fmt_ts(desc.get('stopDate'))}")
    if desc.get("error"):
        print(f"  Error  : {desc['error']}")
    if desc.get("cause"):
        print(f"  Cause  : {desc['cause'][:500]}")

    try:
        sfn_input = json.loads(desc.get("input", "{}"))
    except Exception:
        sfn_input = {}

    # Walk history for Batch job ID
    batch_job_id = None
    try:
        history = sfn.get_execution_history(executionArn=execution_arn,
                                            maxResults=50, reverseOrder=True)
        for ev in history.get("events", []):
            for dkey in ("taskSucceededEventDetails", "taskFailedEventDetails",
                         "lambdaFunctionSucceededEventDetails",
                         "executionFailedEventDetails"):
                detail = ev.get(dkey)
                if not detail:
                    continue
                raw = detail.get("output") or detail.get("cause") or ""
                if not batch_job_id:
                    batch_job_id = _dig(raw, "jobId")
                # Print failures
                etype = ev.get("type", "")
                if "Failed" in etype or "Error" in etype:
                    ts = fmt_ts(ev.get("timestamp"))
                    print(f"\n  [{ts}] {etype}")
                    print(f"    {str(raw)[:300]}")
    except Exception as e:
        print(f"  [WARN] history: {e}")

    return batch_job_id, sfn_input


def _dig(obj, key, depth=0):
    if depth > 5:
        return None
    if isinstance(obj, str):
        try:
            obj = json.loads(obj)
        except Exception:
            return None
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for v in obj.values():
            r = _dig(v, key, depth + 1)
            if r:
                return r
    return None


# ─── AWS Batch ───────────────────────────────────────────────────────────────────
def inspect_batch_job(batch, job_id):
    if not job_id:
        print("  (no Batch job ID)")
        return None, False

    try:
        resp = batch.describe_jobs(jobs=[job_id])
    except Exception as e:
        print(f"  [WARN] {e}")
        return None, False

    jobs = resp.get("jobs", [])
    if not jobs:
        print(f"  Job {job_id} not found.")
        return None, False

    job   = jobs[0]
    queue = job.get("jobQueue", "").split("/")[-1]
    is_gpu = "gpu" in queue.lower()

    print(f"  Job ID : {job_id}")
    print(f"  Name   : {job.get('jobName', '')}")
    print(f"  Queue  : {queue}")
    print(f"  Status : {job.get('status')} — {job.get('statusReason', '')}")
    print(f"  Created: {fmt_ts(job.get('createdAt'))}")
    print(f"  Started: {fmt_ts(job.get('startedAt'))}")
    print(f"  Stopped: {fmt_ts(job.get('stoppedAt'))}")

    log_stream = None
    for i, att in enumerate(job.get("attempts", [])):
        c = att.get("container", {})
        print(f"  Attempt #{i+1}: exitCode={c.get('exitCode','—')}  reason={c.get('reason','')}")
        if c.get("logStreamName"):
            log_stream = c["logStreamName"]
            print(f"    logStream: {log_stream}")

    if not log_stream:
        log_stream = (job.get("container") or {}).get("logStreamName")

    return log_stream, is_gpu


# ─── CloudWatch Logs ─────────────────────────────────────────────────────────────
def fetch_logs(cw, log_stream, is_gpu, max_lines):
    if not log_stream:
        print("  (no log stream)")
        return

    log_group = CW_LOG_GROUP_GPU if is_gpu else CW_LOG_GROUP_CPU
    print(f"  {log_group} / {log_stream}\n")

    for group in (log_group, CW_LOG_GROUP_CPU if is_gpu else CW_LOG_GROUP_GPU):
        try:
            resp = cw.get_log_events(logGroupName=group,
                                     logStreamName=log_stream,
                                     startFromHead=False, limit=max_lines)
            events = resp.get("events", [])
            if events:
                for ev in events:
                    print(f"  {fmt_ts(ev.get('timestamp'))}  {ev.get('message', '').rstrip()}")
                return
        except Exception:
            continue
    print("  (no log events found)")


# ─── S3 Results ──────────────────────────────────────────────────────────────────
def check_results(s3, pipeline_record):
    """Check results/output S3 paths from the pipeline record."""
    output_url = pipeline_record.get("output") or pipeline_record.get("results") or ""

    if output_url and output_url.startswith("s3://"):
        # Parse s3://bucket/key
        parts = output_url.replace("s3://", "").split("/", 1)
        bucket, key = parts[0], parts[1] if len(parts) > 1 else ""
        try:
            resp = s3.head_object(Bucket=bucket, Key=key)
            size = resp.get("ContentLength", 0)
            print(f"  Results ZIP: {output_url}")
            print(f"    Size: {size:,} bytes")
            print(f"    Last modified: {resp.get('LastModified')}")
        except s3.exceptions.NoSuchKey:
            print(f"  Results ZIP: {output_url} (NOT FOUND)")
        except Exception as e:
            print(f"  Results ZIP: {output_url} ({e})")
    else:
        print(f"  Output field: {output_url or '(empty)'}")

    # Also check data bucket for input files
    pipeline_id = pipeline_record.get("pipeline", "")
    user_id     = pipeline_record.get("user_id", "")
    if user_id and pipeline_id:
        prefix = f"CAMRIE/{user_id}/{pipeline_id}/"
        try:
            resp = s3.list_objects_v2(Bucket=DATA_BUCKET, Prefix=prefix, MaxKeys=20)
            items = resp.get("Contents", [])
            if items:
                print(f"\n  Input files (s3://{DATA_BUCKET}/{prefix}):")
                for obj in items:
                    print(f"    {obj['Key'].split('/')[-1]}  ({obj['Size']:,} bytes)")
        except Exception:
            pass


# ─── Download results ─────────────────────────────────────────────────────────────
def download_results(s3_resource, pipeline_record, work_dir):
    """Download the results ZIP."""
    output_url = pipeline_record.get("output") or pipeline_record.get("results") or ""
    if not output_url or not output_url.startswith("s3://"):
        print("  No results to download.")
        return None

    parts = output_url.replace("s3://", "").split("/", 1)
    bucket, key = parts[0], parts[1]
    filename = Path(key).name
    local = work_dir / filename

    if local.exists():
        print(f"  [CACHED] {local}")
    else:
        print(f"  Downloading {output_url}...")
        s3_resource.Bucket(bucket).download_file(key, str(local))
        print(f"  -> {local}")

    return local


def download_inputs(s3_resource, pipeline_record, work_dir):
    """Download input files from the data bucket."""
    pipeline_id = pipeline_record.get("pipeline", "")
    user_id     = pipeline_record.get("user_id", "")
    if not user_id or not pipeline_id:
        print("  Cannot determine input file prefix.")
        return

    prefix = f"CAMRIE/{user_id}/{pipeline_id}/"
    s3_client = s3_resource.meta.client
    resp = s3_client.list_objects_v2(Bucket=DATA_BUCKET, Prefix=prefix, MaxKeys=50)

    data_dir = work_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    for obj in resp.get("Contents", []):
        key = obj["Key"]
        filename = key.split("/")[-1]
        local = data_dir / filename
        if local.exists():
            print(f"  [CACHED] {local}")
        else:
            print(f"  {key} -> {local}")
            s3_resource.Bucket(DATA_BUCKET).download_file(key, str(local))

    return data_dir


def run_locally(work_dir):
    """Run local_test.py with the event.json in work_dir."""
    event_file = work_dir / "event.json"
    if not event_file.exists():
        print(f"  No event.json found in {work_dir}")
        return 1

    local_test = REPO_ROOT / "calculation" / "src" / "local_test.py"
    cmd = [sys.executable, str(local_test), str(event_file)]
    print(f"[RUN] {' '.join(cmd)}")
    sep()
    result = subprocess.run(cmd)
    sep()
    if result.returncode == 0:
        print("[OK] Local run succeeded.")
    else:
        print(f"[FAILED] exit code {result.returncode}")
    return result.returncode


# ─── Main ────────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Inspect a CAMRIE pipeline")
    group  = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--pipeline-id", "-p",
                       help="Pipeline ID (e.g. 64730201-9642-4a66-bfc8-65fa93573e90)")
    group.add_argument("--index", "-i", type=int,
                       help="Index from debug-failed-tasks.json (0 = most recent)")

    parser.add_argument("--failed-only", action="store_true",
                        help="Use failed list for --index")
    parser.add_argument("--profile", default=os.getenv("AWS_PROFILE", "nyu"),
                        help="AWS IAM profile (default: nyu)")
    parser.add_argument("--log-lines", type=int, default=100,
                        help="CloudWatch lines (default: 100)")
    parser.add_argument("--download-only", "-d", action="store_true",
                        help="Download results + inputs to local dir")
    parser.add_argument("--run-local", "-r", action="store_true",
                        help="Download + run locally")
    parser.add_argument("--work-dir", "-w", help="Work dir (default: temp)")
    args = parser.parse_args()

    sess  = aws_session(args.profile)
    ddb   = sess.client("dynamodb")
    sfn   = sess.client("stepfunctions")
    batch = sess.client("batch")
    cw    = sess.client("logs")
    s3    = sess.client("s3")
    s3_res = sess.resource("s3")

    # 1. Resolve pipeline
    if args.pipeline_id:
        pipeline_id = args.pipeline_id
    else:
        data  = load_tasks()
        entry = find_task_by_index(data, args.index, args.failed_only)
        pipeline_id = entry.get("pipeline", entry.get("pipeline_id", ""))
        if not pipeline_id:
            print("[ERROR] No pipeline ID in entry.")
            sys.exit(1)

    # 2. Get pipeline record from DynamoDB
    sep(f"Pipeline: {pipeline_id}")
    record = get_pipeline_from_ddb(ddb, pipeline_id)
    if not record:
        print(f"  [ERROR] Pipeline not found in {PIPELINES_TABLE}")
        sys.exit(1)

    print(f"  Status   : {record.get('status')}")
    print(f"  Alias    : {record.get('alias', '—')}")
    print(f"  Created  : {record.get('created_at', '—')}")
    print(f"  Updated  : {record.get('updated_at', '—')}")
    print(f"  User     : {record.get('user_id', '—')}")
    print(f"  Mode     : {record.get('mode', '—')}")

    # 3. Step Functions (try to find matching execution)
    sep("Step Functions")
    exec_arn = find_execution_for_pipeline(sfn, pipeline_id,
                                            record.get("created_at"))
    batch_job_id = None
    sfn_input = None
    if exec_arn:
        batch_job_id, sfn_input = inspect_execution(sfn, exec_arn)
    else:
        print("  (no matching execution found — may have been cleaned up)")

    # 4. Batch
    sep("AWS Batch")
    log_stream, is_gpu = inspect_batch_job(batch, batch_job_id)

    # 5. CloudWatch
    sep("CloudWatch Logs")
    fetch_logs(cw, log_stream, is_gpu, args.log_lines)

    # 6. Results / S3
    sep("Results & S3 Artifacts")
    check_results(s3, record)

    # 7. Download / run locally
    if args.download_only or args.run_local:
        work_dir = Path(args.work_dir) if args.work_dir else DEBUG_DIR / pipeline_id
        work_dir.mkdir(parents=True, exist_ok=True)
        sep(f"Download: {work_dir}")

        download_results(s3_res, record, work_dir)
        download_inputs(s3_res, record, work_dir)

        if args.run_local:
            sys.exit(run_locally(work_dir))
        else:
            print(f"\n  Files saved to: {work_dir}")

    sep()


if __name__ == "__main__":
    main()
