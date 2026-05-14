#!/usr/bin/env python3
"""
Run a CAMRIE simulation on the cloud via CloudMR Brain API.

Uploads local phantom files (rho, t1, t2) and a pulse sequence,
then submits a queue_job request and polls until completion.

Usage:
    python scripts/run_cloud_test.py \
        --api-user you@example.com \
        --api-pass YourPassword \
        --seq-file /path/to/sequence.seq \
        [--phantom-dir calculation/phantom] \
        [--num-slices 4] \
        [--b0 1.5] \
        [--spin-factor 4]
"""

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import boto3
import requests

# ─── Defaults ──────────────────────────────────────────────────────────────────
API_BASE = "https://brain.aws.cloudmrhub.com/Prod/api"
CLOUDAPP_NAME = "CAMRIE"
CHUNK_SIZE = 10 * 1024 * 1024  # 10 MB


# ─── Helpers ───────────────────────────────────────────────────────────────────
def info(msg):  print(f"  → {msg}")
def ok(msg):    print(f"  ✓ {msg}")
def fail(msg):  print(f"  ✗ {msg}")


def md5_file(path):
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def login(base_url, email, password):
    """Login and return (id_token, user_id)."""
    r = requests.post(f"{base_url}/auth/login", json={
        "email": email,
        "password": password,
    })
    r.raise_for_status()
    data = r.json()
    return data["id_token"], data["user_id"]


def upload_file(base_url, token, local_path, cloudapp_name=CLOUDAPP_NAME):
    """
    Upload a local file via the multipart upload API.
    Returns the S3 key.
    """
    local_path = Path(local_path)
    file_size = local_path.stat().st_size
    file_md5 = md5_file(local_path)
    headers = {"Authorization": f"Bearer {token}"}

    # 1. Initiate
    info(f"Initiating upload: {local_path.name} ({file_size} bytes)")
    r = requests.post(f"{base_url}/upload_initiate", headers=headers, json={
        "filename": local_path.name,
        "filetype": "application/octet-stream",
        "filesize": file_size,
        "filemd5": file_md5,
        "cloudapp_name": cloudapp_name,
    })
    r.raise_for_status()
    init = r.json()
    s3_key = init["Key"]
    upload_id = init["uploadId"]
    part_urls = init["partUrls"]

    # 2. Upload parts
    parts = []
    with open(local_path, "rb") as f:
        for i, url in enumerate(part_urls, start=1):
            chunk = f.read(CHUNK_SIZE)
            if not chunk:
                break
            pr = requests.put(url, data=chunk)
            pr.raise_for_status()
            etag = pr.headers.get("ETag", "").strip('"')
            parts.append({"partNumber": i, "etag": etag})

    # 3. Finalize
    r = requests.post(f"{base_url}/upload_finalize", headers=headers, json={
        "uploadId": upload_id,
        "Key": s3_key,
        "parts": parts,
    })
    r.raise_for_status()
    ok(f"Uploaded → s3://.../{s3_key}")
    return s3_key


def queue_job(base_url, token, file_keys, sim_config):
    """Submit a CAMRIE job. Returns the response dict."""
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    data_bucket = "cloudmr-data-cloudmrhub-brain-us-east-1"

    payload = {
        "cloudapp_name": CLOUDAPP_NAME,
        "alias": sim_config.get("alias", "Cloud Test – Cylindrical Phantom"),
        "mode": "mode_1",
        "task": {
            "options": {
                "rho": {
                    "type": "s3",
                    "bucket": data_bucket,
                    "key": file_keys["rho"],
                    "filename": "rho.nii",
                },
                "t1": {
                    "type": "s3",
                    "bucket": data_bucket,
                    "key": file_keys["t1"],
                    "filename": "t1.nii",
                },
                "t2": {
                    "type": "s3",
                    "bucket": data_bucket,
                    "key": file_keys["t2"],
                    "filename": "t2.nii",
                },
                "sequence": {
                    "type": "s3",
                    "bucket": data_bucket,
                    "key": file_keys["seq"],
                    "filename": Path(file_keys["seq"]).name,
                },
                "geometry": {
                    "isocenter_mm": None,
                    "slice_normal": sim_config.get("slice_normal", [0, 0, 1]),
                    "num_slices": sim_config.get("num_slices", 4),
                    "slice_thickness_mm": None,
                    "slice_gap_mm": 0.0,
                },
                "simulation": {
                    "b0": sim_config.get("b0", 1.5),
                    "spin_factor": sim_config.get("spin_factor", 4),
                    "n_threads": sim_config.get("n_threads", 4),
                    "use_gpu": sim_config.get("use_gpu", False),
                    "apply_hamming": True,
                    "spins_per_voxel": sim_config.get("spins_per_voxel", 1),
                    "parallel_slices": sim_config.get("parallel_slices", 1),
                    "slice_padding": sim_config.get("slice_padding", 0.5),
                },
            }
        },
        "output": None,
        "computing_unit_id": None,
    }

    r = requests.post(f"{base_url}/pipeline/queue_job", headers=headers, json=payload)
    r.raise_for_status()
    return r.json()


def start_log_tail(log_group, aws_profile, aws_region, since="1m", task_id=None):
    """Start a background process that tails CloudWatch logs."""
    cmd = [
        "aws", "logs", "tail", log_group,
        "--follow",
        "--since", since,
        "--profile", aws_profile,
        "--region", aws_region,
        "--format", "short",
    ]
    # Derive stream prefix from task_id.
    # Fargate stream: camrie/camrie-worker/{task_id}
    # GPU stream:     camrie-gpu/camrie-worker/{task_id}
    if task_id:
        if "gpu" in log_group:
            prefix = f"camrie-gpu/camrie-worker/{task_id}"
        else:
            prefix = f"camrie/camrie-worker/{task_id}"
        cmd.extend(["--log-stream-name-prefix", prefix])
    info(f"Tailing logs: {log_group}" + (f" (task {task_id})" if task_id else ""))
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        return proc
    except FileNotFoundError:
        fail("AWS CLI not found — cannot tail logs.")
        return None


def wait_for_log_stream(log_group, task_id, aws_profile, aws_region, timeout=1800):
    """
    Block until the task's CloudWatch log stream appears and has at least one event.
    Prints a heartbeat every 30s so you know it's not stuck.
    Returns True when logs appear, False on timeout.
    """
    import boto3
    sess = boto3.Session(profile_name=aws_profile, region_name=aws_region)
    cw   = sess.client("logs")
    prefix = f"camrie-gpu/camrie-worker/{task_id}" if "gpu" in log_group \
             else f"camrie/camrie-worker/{task_id}"
    t0 = time.time()
    last_msg = 0
    print(f"  Waiting for log stream: {prefix}")
    print(f"  (GPU: Julia loads precompiled cache (~30s), then CUDA PTX JIT (~5-10 min on fresh instance)")
    while time.time() - t0 < timeout:
        elapsed = time.time() - t0
        try:
            resp = cw.get_log_events(
                logGroupName=log_group,
                logStreamName=prefix,
                limit=5,
                startFromHead=True,
            )
            if resp.get("events"):
                print(f"  [{elapsed:.0f}s] First log line appeared — container is alive")
                return True
        except cw.exceptions.ResourceNotFoundException:
            pass
        except Exception as e:
            pass
        if elapsed - last_msg >= 30:
            # Show ECS task status as heartbeat
            try:
                ecs = sess.client("ecs")
                cluster = "camrie-app-prod-cluster"
                tasks = ecs.list_tasks(cluster=cluster, desiredStatus="RUNNING")["taskArns"]
                running = len(tasks)
            except Exception:
                running = "?"
            print(f"  [{elapsed:.0f}s] Still initializing... (ECS running tasks: {running}) — waiting for first log line")
            last_msg = elapsed
        time.sleep(10)
    print(f"  Timeout waiting for log stream after {timeout}s")
    return False


def stream_log_lines(proc, max_lines=None):
    """Read and print available lines from the log tail process (non-blocking)."""
    import select
    count = 0
    while True:
        ready, _, _ = select.select([proc.stdout], [], [], 0.1)
        if not ready:
            break
        line = proc.stdout.readline()
        if not line:
            break
        print(f"    {line.rstrip()}")
        count += 1
        if max_lines and count >= max_lines:
            break
    return count


# ─── GPU / ECS monitoring ─────────────────────────────────────────────────────
# On-demand prices us-east-1 (update if needed)
_COST_PER_HOUR = {
    "g4dn.xlarge":  0.526,
    "g5.xlarge":    1.006,
    "fargate":      0.210,   # 4 vCPU + 16 GB approx
}


def _boto_session(aws_profile, aws_region):
    return boto3.Session(profile_name=aws_profile, region_name=aws_region)


def find_ecs_task_from_execution(execution_arn, cluster, aws_profile, aws_region):
    """Return the ECS task ARN launched by a Step Functions execution."""
    sess = _boto_session(aws_profile, aws_region)
    sfn  = sess.client("stepfunctions")
    ecs  = sess.client("ecs")
    try:
        history = sfn.get_execution_history(executionArn=execution_arn, maxResults=20)
        for ev in history["events"]:
            detail = ev.get("taskSucceededEventDetails") or ev.get("taskStateExitedEventDetails")
            if detail:
                out = json.loads(detail.get("output", "{}"))
                arn = out.get("taskArn")
                if arn:
                    return arn
        # Fallback: list recent tasks in cluster
        for state in ("RUNNING", "PENDING", "STOPPED"):
            resp = ecs.list_tasks(cluster=cluster, desiredStatus=state)
            if resp["taskArns"]:
                return resp["taskArns"][0]
    except Exception as e:
        info(f"Could not resolve ECS task ARN: {e}")
    return None


def monitor_ecs_task(task_arn, cluster, aws_profile, aws_region, use_gpu=False):
    """
    Poll ECS task lifecycle and print stage transitions with timestamps.
    Returns a dict with timing info for cost calculation.
    """
    if not task_arn:
        return {}

    sess = _boto_session(aws_profile, aws_region)
    ecs  = sess.client("ecs")
    task_id = task_arn.split("/")[-1]
    instance_type = "g4dn.xlarge" if use_gpu else "fargate"

    print(f"\n═══ ECS Task Monitor ═══")
    info(f"Task: {task_id}")
    info(f"Instance: {instance_type}")

    seen_status   = None
    t_submitted   = time.time()
    t_running     = None
    t_stopped     = None
    last_ec2_msg  = None

    for _ in range(120):   # up to 30 min
        try:
            resp  = ecs.describe_tasks(cluster=cluster, tasks=[task_arn])
            tasks = resp.get("tasks", [])
            if not tasks:
                time.sleep(15)
                continue
            task   = tasks[0]
            status = task["lastStatus"]

            if status != seen_status:
                elapsed = time.time() - t_submitted
                ts      = datetime.now().strftime("%H:%M:%S")
                if status == "PROVISIONING":
                    print(f"  [{ts}] +{elapsed:5.0f}s  PROVISIONING  (waiting for EC2 instance to register)")
                elif status == "PENDING":
                    print(f"  [{ts}] +{elapsed:5.0f}s  PENDING       (instance ready, pulling image)")
                elif status == "RUNNING":
                    t_running = time.time()
                    cold = elapsed
                    print(f"  [{ts}] +{elapsed:5.0f}s  RUNNING       (container started — cold start was {cold:.0f}s)")
                elif status == "DEPROVISIONING":
                    print(f"  [{ts}] +{elapsed:5.0f}s  DEPROVISIONING")
                elif status == "STOPPED":
                    t_stopped = time.time()
                    reason = task.get("stoppedReason", "")
                    exit_code = None
                    for c in task.get("containers", []):
                        if c.get("exitCode") is not None:
                            exit_code = c["exitCode"]
                    print(f"  [{ts}] +{elapsed:5.0f}s  STOPPED       exit={exit_code} reason={reason}")
                seen_status = status

            if status == "STOPPED":
                break

            # For GPU: show capacity provider activity message once
            if use_gpu and status == "PROVISIONING":
                try:
                    for attr in task.get("attributes", []):
                        if "capacityProvider" in attr.get("name", ""):
                            msg = attr.get("value", "")
                            if msg and msg != last_ec2_msg:
                                info(f"  CP: {msg}")
                                last_ec2_msg = msg
                except Exception:
                    pass

        except Exception as e:
            info(f"ECS describe error: {e}")

        time.sleep(15)

    # Cost summary
    total_s   = (t_stopped or time.time()) - t_submitted
    running_s = (t_stopped or time.time()) - (t_running or t_submitted)
    cold_s    = total_s - running_s
    rate      = _COST_PER_HOUR.get(instance_type, 0)
    # EC2 billing: per-second, minimum 60s
    billed_s  = max(running_s, 60) if use_gpu else running_s
    cost      = (billed_s / 3600) * rate

    print(f"\n  ── Cost estimate ({instance_type}) ──")
    print(f"  Cold start:    {cold_s:6.0f}s  (not billed for Fargate; EC2 billed from launch)")
    print(f"  Simulation:    {running_s:6.0f}s")
    print(f"  Total:         {total_s:6.0f}s")
    print(f"  Rate:          ${rate:.4f}/hr")
    print(f"  Est. cost:     ${cost:.4f}")
    if use_gpu:
        ec2_s = total_s   # EC2 billed from the moment instance starts
        ec2_cost = (max(ec2_s, 60) / 3600) * rate
        print(f"  EC2 full run:  ${ec2_cost:.4f}  (instance billed from PROVISIONING)")

    return {"task_id": task_id, "total_s": total_s, "running_s": running_s,
            "cold_s": cold_s, "cost_usd": cost}


def download_result(results, output_dir, aws_profile, aws_region):
    """
    Download and unzip the simulation result from S3.

    Expects results dict with 'bucket' and 'key' (as uploaded by the pipeline).
    Falls back to scanning the results S3 bucket for the most recent zip if
    the pipeline response does not carry explicit bucket/key fields.

    Args:
        results:    dict from pipeline response (may contain bucket/key)
        output_dir: local directory to extract into (created if needed)
        aws_profile: AWS CLI profile
        aws_region:  AWS region
    """
    import zipfile
    import io

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    sess = boto3.Session(profile_name=aws_profile, region_name=aws_region)
    s3 = sess.client("s3")

    bucket = None
    key    = None

    # Try direct fields first
    if isinstance(results, dict):
        bucket = results.get("bucket") or results.get("Bucket")
        key    = results.get("key")    or results.get("Key")
        # Some responses nest under 'output' or 'data'
        if not key:
            for sub in (results.get("output") or {}, results.get("data") or {}):
                if isinstance(sub, dict):
                    bucket = bucket or sub.get("bucket") or sub.get("Bucket")
                    key    = key    or sub.get("key")    or sub.get("Key")

    # Fallback: scan the results bucket for the most recent zip
    if not key:
        default_bucket = "cloudmr-results-cloudmrhub-brain-us-east-1"
        bucket = bucket or default_bucket
        info(f"No explicit S3 key in results — scanning {bucket} for most recent zip...")
        try:
            resp = s3.list_objects_v2(Bucket=bucket, MaxKeys=20)
            zips = [o for o in resp.get("Contents", []) if o["Key"].endswith(".zip")]
            if zips:
                zips.sort(key=lambda o: o["LastModified"], reverse=True)
                key = zips[0]["Key"]
                info(f"Found: {key}  (last modified {zips[0]['LastModified']}")
            else:
                fail("No zip files found in results bucket.")
                return
        except Exception as e:
            fail(f"Could not list results bucket: {e}")
            return

    zip_name = Path(key).name
    local_zip = output_dir / zip_name

    print(f"\n═══ Step 6: Download Results ═══")
    info(f"Bucket: {bucket}")
    info(f"Key:    {key}")
    info(f"Downloading → {local_zip} ...")

    try:
        s3.download_file(bucket, key, str(local_zip))
    except Exception as e:
        fail(f"S3 download failed: {e}")
        return

    ok(f"Downloaded {local_zip.stat().st_size / 1024:.1f} KB")

    # Unzip
    info(f"Extracting to {output_dir} ...")
    try:
        with zipfile.ZipFile(local_zip, "r") as zf:
            zf.extractall(output_dir)
        extracted = [str(p.relative_to(output_dir)) for p in output_dir.rglob("*") if p.is_file() and p != local_zip]
        ok(f"Extracted {len(extracted)} file(s):")
        for f in extracted:
            print(f"    {f}")
    except zipfile.BadZipFile as e:
        fail(f"Could not unzip {local_zip}: {e}")


def tail_logs_blocking(log_group, aws_profile, aws_region, task_id=None):
    """Tail CloudWatch logs until interrupted. Optionally filter by task ID."""
    cmd = [
        "aws", "logs", "tail", log_group,
        "--follow",
        "--since", "5m",
        "--profile", aws_profile,
        "--region", aws_region,
        "--format", "short",
    ]
    if task_id:
        # Filter to a specific task's log stream
        cmd.extend(["--log-stream-name-prefix", f"camrie/camrie-worker/{task_id}"])

    info(f"Tailing logs: {log_group}" + (f" (task {task_id})" if task_id else ""))
    info("Press Ctrl+C to stop")
    try:
        subprocess.run(cmd, check=False)
    except KeyboardInterrupt:
        print()


def poll_pipeline(base_url, token, pipeline_id, timeout=600, interval=15,
                  log_proc=None):
    """Poll pipeline status until completed, failed, or timeout."""
    headers = {"Authorization": f"Bearer {token}"}
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            r = requests.get(f"{base_url}/pipeline/{pipeline_id}", headers=headers)
            if r.status_code == 200:
                data = r.json()
                # API returns [bool, pipeline_dict] via get_pipeline() → with_handler
                # Handle: [True, {...}], [{...}], {...}, True, etc.
                if isinstance(data, list) and len(data) == 2 and isinstance(data[0], bool):
                    # [True, {pipeline}] or [False, {error}]
                    if data[0] and isinstance(data[1], dict):
                        data = data[1]
                    else:
                        elapsed = time.time() - t0
                        info(f"[{elapsed:.0f}s] Pipeline lookup failed: {data[1]}")
                        time.sleep(interval)
                        continue
                elif isinstance(data, list) and len(data) >= 1 and isinstance(data[0], dict):
                    data = data[0]
                elif isinstance(data, list) and len(data) == 1 and isinstance(data[0], list):
                    # [[{...}, {...}]] — list of pipelines wrapped
                    items = data[0]
                    data = items[0] if items else {}
                if not isinstance(data, dict):
                    elapsed = time.time() - t0
                    info(f"[{elapsed:.0f}s] Unexpected response: {str(data)[:100]}")
                    if log_proc:
                        stream_log_lines(log_proc)
                    time.sleep(interval)
                    continue
                status = data.get("status", "unknown")
                elapsed = time.time() - t0
                info(f"[{elapsed:.0f}s] Pipeline status: {status}")
                if status in ("completed", "failed"):
                    if log_proc:
                        # Drain remaining log lines
                        time.sleep(2)
                        stream_log_lines(log_proc)
                        log_proc.terminate()
                    return data
            else:
                elapsed = time.time() - t0
                info(f"[{elapsed:.0f}s] HTTP {r.status_code} — retrying…")
        except Exception as e:
            elapsed = time.time() - t0
            info(f"[{elapsed:.0f}s] Poll error: {e} — retrying…")
        # Stream log lines between polls
        if log_proc:
            stream_log_lines(log_proc)
        time.sleep(interval)
    fail(f"Timeout after {timeout}s")
    return None


# ─── Main ──────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Run CAMRIE cloud simulation test")
    parser.add_argument("--api-base", default=API_BASE, help="CloudMR Brain API base URL")
    parser.add_argument("--token", default=None,
                        help="JWT id_token (skip login). Mutually exclusive with --api-user/--api-pass")
    parser.add_argument("--api-user", default=None, help="CloudMR email (if no --token)")
    parser.add_argument("--api-pass", default=None, help="CloudMR password (if no --token)")
    parser.add_argument("--phantom-dir", default="calculation/phantom",
                        help="Directory with rho.nii, t1.nii, t2.nii")
    parser.add_argument("--seq-file", required=False, default=None,
                        help="Path to Pulseq .seq file (not needed with --logs-only)")
    parser.add_argument("--alias", default="Cloud Test – Cylindrical Phantom",
                        help="Pipeline alias")

    # Simulation parameters
    parser.add_argument("--b0", type=float, default=1.5)
    parser.add_argument("--num-slices", type=int, default=4)
    parser.add_argument("--spin-factor", type=int, default=4)
    parser.add_argument("--spins-per-voxel", type=int, default=1)
    parser.add_argument("--parallel-slices", type=int, default=1)
    parser.add_argument("--slice-padding", type=float, default=0.5)
    parser.add_argument("--n-threads", type=int, default=4)
    parser.add_argument("--use-gpu", action="store_true")
    parser.add_argument("--timeout", type=int, default=600,
                        help="Max seconds to wait for completion")
    parser.add_argument("--no-poll", action="store_true",
                        help="Submit and exit without polling")

    # AWS / log options
    parser.add_argument("--aws-profile", default="nyu",
                        help="AWS CLI profile for log tailing (default: nyu)")
    parser.add_argument("--aws-region", default="us-east-1",
                        help="AWS region (default: us-east-1)")
    parser.add_argument("--log-group", default=None,
                        help="CloudWatch log group (auto: /ecs/camrie-Prod or /ecs/camrie-gpu-Prod)")
    parser.add_argument("--tail-logs", action="store_true",
                        help="Tail CloudWatch logs while polling")
    parser.add_argument("--monitor-task", action="store_true",
                        help="Monitor ECS task lifecycle and show cost estimate (GPU recommended)")
    parser.add_argument("--cluster", default="camrie-app-prod-cluster",
                        help="ECS cluster name")
    parser.add_argument("--logs-only", default=None, metavar="TASK_ID",
                        help="Skip upload/submit — just tail logs for a Fargate task ID "
                             "(e.g. 74f378a959ff4ba4be598326ef73cf13)")
    parser.add_argument("--output-dir", default=None, metavar="DIR",
                        help="Download and unzip the result zip into this directory "
                             "when the simulation completes")

    args = parser.parse_args()

    # Auto-select log group
    if not args.log_group:
        args.log_group = "/ecs/camrie-gpu-Prod" if args.use_gpu else "/ecs/camrie-Prod"

    # ── Logs-only mode ─────────────────────────────────────────────────────
    if args.logs_only:
        tail_logs_blocking(args.log_group, args.aws_profile, args.aws_region,
                           task_id=args.logs_only)
        return

    # Validate --seq-file is provided for normal mode
    if not args.seq_file:
        fail("--seq-file is required (unless using --logs-only)")
        sys.exit(1)

    phantom_dir = Path(args.phantom_dir)

    # Validate files
    rho_path = phantom_dir / "rho.nii"
    t1_path = phantom_dir / "t1.nii"
    t2_path = phantom_dir / "t2.nii"
    seq_path = Path(args.seq_file)

    for p in [rho_path, t1_path, t2_path, seq_path]:
        if not p.exists():
            fail(f"File not found: {p}")
            sys.exit(1)

    # ── Step 1: Login ──────────────────────────────────────────────────────────
    print("\n═══ Step 1: Login ═══")
    if args.token:
        token = args.token
        user_id = "(from token)"
        ok(f"Using provided JWT token")
    elif args.api_user and args.api_pass:
        token, user_id = login(args.api_base, args.api_user, args.api_pass)
        ok(f"Logged in as {args.api_user} (user_id={user_id})")
    else:
        fail("Provide either --token or both --api-user and --api-pass")
        sys.exit(1)

    # ── Step 2: Upload files ───────────────────────────────────────────────────
    print("\n═══ Step 2: Upload Files ═══")
    file_keys = {}
    file_keys["rho"] = upload_file(args.api_base, token, rho_path)
    file_keys["t1"] = upload_file(args.api_base, token, t1_path)
    file_keys["t2"] = upload_file(args.api_base, token, t2_path)
    file_keys["seq"] = upload_file(args.api_base, token, seq_path)

    # ── Step 3: Queue job ──────────────────────────────────────────────────────
    print("\n═══ Step 3: Queue Job ═══")
    sim_config = {
        "alias": args.alias,
        "b0": args.b0,
        "num_slices": args.num_slices,
        "spin_factor": args.spin_factor,
        "spins_per_voxel": args.spins_per_voxel,
        "parallel_slices": args.parallel_slices,
        "slice_padding": args.slice_padding,
        "n_threads": args.n_threads,
        "use_gpu": args.use_gpu,
    }
    result = queue_job(args.api_base, token, file_keys, sim_config)
    pipeline_id = result.get("pipeline")
    execution_arn = result.get('executionArn', '')
    ok(f"Job queued!")
    info(f"Pipeline:      {pipeline_id}")
    info(f"Execution ARN: {execution_arn}")
    info(f"Log group:     {args.log_group}")
    if args.use_gpu:
        info(f"Compute:       GPU (g4dn.xlarge / EC2-backed ECS)")
    else:
        info(f"Compute:       CPU (Fargate 4vCPU/16GB)")
    if result.get("computingUnit"):
        cu = result["computingUnit"]
        info(f"Computing Unit: {cu.get('alias')} ({cu.get('mode')}) id={cu.get('id')}")

    if args.no_poll:
        print(f"\n  Use pipeline ID to check status:")
        print(f"  GET {args.api_base}/pipeline/{pipeline_id}")
        return

    # ── Step 4: ECS task monitor (optional) ───────────────────────────────────
    ecs_task_id = None
    if args.monitor_task or args.use_gpu:
        # Give Step Functions a moment to launch the ECS task
        info("Waiting 10s for ECS task to be registered...")
        time.sleep(10)
        task_arn = find_ecs_task_from_execution(
            execution_arn, args.cluster, args.aws_profile, args.aws_region)
        if task_arn:
            ecs_task_id = task_arn.split("/")[-1]
            import threading
            monitor_thread = threading.Thread(
                target=monitor_ecs_task,
                args=(task_arn, args.cluster, args.aws_profile, args.aws_region, args.use_gpu),
                daemon=True,
            )
            monitor_thread.start()
        else:
            info("Could not find ECS task ARN — skipping task monitor")

    # ── Step 5: Poll ───────────────────────────────────────────────────────────
    print(f"\n═══ Step 5: Polling (timeout={args.timeout}s) ═══")

    log_proc = None
    if args.tail_logs:
        # For GPU: wait for the log stream to appear before starting tail
        # (stream doesn't exist until container writes its first line)
        if args.use_gpu and ecs_task_id:
            wait_for_log_stream(args.log_group, ecs_task_id,
                                args.aws_profile, args.aws_region)
        log_proc = start_log_tail(args.log_group, args.aws_profile, args.aws_region,
                                  since="5m", task_id=ecs_task_id)

    final = poll_pipeline(args.api_base, token, pipeline_id,
                          timeout=args.timeout, interval=15,
                          log_proc=log_proc)
    if final:
        status = final.get("status", "unknown")
        if status == "completed":
            ok(f"Simulation completed!")
            if final.get("results"):
                info(f"Results: {json.dumps(final['results'], indent=2)}")
            if args.output_dir:
                download_result(
                    final.get("results") or {},
                    args.output_dir,
                    args.aws_profile,
                    args.aws_region,
                )
        else:
            fail(f"Simulation ended with status: {status}")
            if final.get("log"):
                info(f"Log: {final['log']}")
    if log_proc and log_proc.poll() is None:
        log_proc.terminate()
    print()


if __name__ == "__main__":
    main()
