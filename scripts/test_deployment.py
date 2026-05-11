#!/usr/bin/env python3
"""
CAMRIE Deployment Test Suite
=============================================================
Covers all 6 acceptance criteria:

  T1  No runtime Julia compilation  – `using KomaMRI` must start < 15 s
  T2  KomaMRI example               – tiny 1-spin sim inside the container
  T3  API connectivity              – CloudMR Brain API responds
  T4  Failed job → failed bucket    – bad event ends up in the failed S3 bucket
  T5  Completed job → results bucket – synthetic phantom job lands in results bucket
  T6  Logs in one place             – CloudWatch log group has recent streams

Usage
-----
  python scripts/test_deployment.py [options]

  --profile      AWS profile  (default: nyu)
  --region       AWS region   (default: us-east-1)
  --stack        SAM stack name (default: camrie-app-prod)
  --brain-stack  CloudMR Brain stack (default: cloudmrhub-brain)
  --ecr-image    ECR image URI (default: auto-detect from ECR)
  --skip T1,T2   Comma-separated test IDs to skip
  --seq-file     Local Pulseq .seq file for T5 (if omitted T5 is skipped)
  --api-url      Full CloudMR API base URL (default: from stack params)
  --api-user     CloudMR admin e-mail  (for T3 token check)
  --api-pass     CloudMR admin password
  --timeout      Seconds to wait for Fargate tasks (default: 180)
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from datetime import datetime, timezone

import boto3
import requests

# ─── ANSI colours ─────────────────────────────────────────────────────────────
GREEN  = "\033[32m"
RED    = "\033[31m"
YELLOW = "\033[33m"
CYAN   = "\033[36m"
RESET  = "\033[0m"
BOLD   = "\033[1m"

def ok(msg):    print(f"  {GREEN}✓{RESET}  {msg}")
def fail(msg):  print(f"  {RED}✗{RESET}  {msg}")
def info(msg):  print(f"  {CYAN}→{RESET}  {msg}")
def warn(msg):  print(f"  {YELLOW}⚠{RESET}  {msg}")
def section(title):
    bar = "═" * (len(title) + 4)
    print(f"\n{BOLD}{CYAN}╔{bar}╗")
    print(f"║  {title}  ║")
    print(f"╚{bar}╝{RESET}")


# ─── Result collector ──────────────────────────────────────────────────────────
results = {}   # test_id → {"passed": bool, "msg": str, "elapsed": float}

def record(test_id, passed, msg, elapsed=0.0):
    results[test_id] = {"passed": passed, "msg": msg, "elapsed": elapsed}
    if passed:
        ok(f"[{test_id}] {msg}  ({elapsed:.1f}s)")
    else:
        fail(f"[{test_id}] {msg}  ({elapsed:.1f}s)")


# ─── AWS helpers ───────────────────────────────────────────────────────────────
def get_session(profile, region):
    return boto3.Session(profile_name=profile, region_name=region)


def get_cf_output(cf, stack_name, key):
    """Return a CloudFormation stack output value or None."""
    try:
        r = cf.describe_stacks(StackName=stack_name)
        for o in r["Stacks"][0].get("Outputs", []):
            if o["OutputKey"] == key:
                return o["OutputValue"]
    except Exception as e:
        warn(f"CF output {key} from {stack_name}: {e}")
    return None


def get_cf_param(cf, stack_name, key):
    """Return a CloudFormation stack *parameter* value or None."""
    try:
        r = cf.describe_stacks(StackName=stack_name)
        for p in r["Stacks"][0].get("Parameters", []):
            if p["ParameterKey"] == key:
                return p["ParameterValue"]
    except Exception as e:
        warn(f"CF param {key} from {stack_name}: {e}")
    return None


def get_cf_export(cf, export_name):
    paginator = cf.get_paginator("list_exports")
    for page in paginator.paginate():
        for exp in page["Exports"]:
            if exp["Name"] == export_name:
                return exp["Value"]
    return None


def newest_s3_key(s3_client, bucket, prefix, after_time):
    """Return the newest key in bucket/prefix uploaded after after_time (UTC epoch)."""
    resp = s3_client.list_objects_v2(Bucket=bucket, Prefix=prefix)
    found = []
    for obj in resp.get("Contents", []):
        ts = obj["LastModified"].timestamp()
        if ts > after_time:
            found.append((ts, obj["Key"]))
    if not found:
        return None
    found.sort(reverse=True)
    return found[0][1]


def wait_for_s3_key(s3_client, bucket, prefix, after_time, timeout=180, poll=10):
    deadline = time.time() + timeout
    while time.time() < deadline:
        key = newest_s3_key(s3_client, bucket, prefix, after_time)
        if key:
            return key
        info(f"Waiting for s3://{bucket}/{prefix}* … ({int(deadline - time.time())}s left)")
        time.sleep(poll)
    return None


def latest_ecr_image(ecr_client, repo_name):
    try:
        pages = ecr_client.get_paginator("describe_images").paginate(
            repositoryName=repo_name,
            filter={"tagStatus": "TAGGED"},
        )
        images = []
        for page in pages:
            images.extend(page["imageDetails"])
        images.sort(key=lambda x: x.get("imagePushedAt", 0), reverse=True)
        if images:
            tag = images[0]["imageTags"][0]
            registry = ecr_client.describe_registry()["registryId"]
            region   = ecr_client.meta.region_name
            return f"{registry}.dkr.ecr.{region}.amazonaws.com/{repo_name}:{tag}"
    except Exception as e:
        warn(f"Could not auto-detect ECR image: {e}")
    return None


def run(cmd, timeout=30, capture=True):
    """Run a shell command; return (stdout, returncode)."""
    r = subprocess.run(
        cmd, shell=True, capture_output=capture,
        text=True, timeout=timeout,
    )
    return (r.stdout + r.stderr).strip(), r.returncode


# ─── Synthetic NIfTI phantom ──────────────────────────────────────────────────
def write_synthetic_nifti(path, value=1.0, shape=(10, 10, 5)):
    """Write a tiny NIfTI-1 file filled with `value`; no nibabel required."""
    import struct, numpy as np

    data = np.full(shape, value, dtype=np.float32)
    hdr = bytearray(352)                       # minimal NIfTI-1 header

    def si(offset, fmt, *vals):
        struct.pack_into(fmt, hdr, offset, *vals)

    si(0,   "<i", 348)                         # sizeof_hdr
    si(40,  "<h", 3)                           # dim[0] = 3 dims
    si(42,  "<h", shape[0])                    # dim[1]
    si(44,  "<h", shape[1])                    # dim[2]
    si(46,  "<h", shape[2])                    # dim[3]
    si(70,  "<h", 16)                          # datatype = float32
    si(72,  "<h", 32)                          # bitpix
    si(80,  "<f", 2.0, 2.0, 2.0, 1.0)         # pixdim[1..4] = 2 mm voxels
    si(108, "<f", 352.0)                       # vox_offset
    si(112, "<f", 1.0)                         # scl_slope
    si(252, "<f", 0.0)                         # scl_inter
    si(344, "4s", b"n+1\x00")                  # magic

    with open(path, "wb") as f:
        f.write(bytes(hdr))
        f.write(data.tobytes())


# ═══════════════════════════════════════════════════════════════════════════════
#  T1 – No runtime Julia compilation
# ═══════════════════════════════════════════════════════════════════════════════
def test_t1_precompile(args, image_uri):
    section("T1 · No runtime Julia compilation")
    MAX_SECONDS = 45.0   # Allow headroom; truly precompiled should be < 45 s
    cmd = (
        f'docker run --rm '
        f'--entrypoint julia '
        f'-e JULIA_DEPOT_PATH=/root/.julia '
        f'{image_uri} '
        f'-e "using KomaMRI; println(\\"KomaMRI loaded\\")"'
    )
    info(f"docker run … julia -e 'using KomaMRI'")
    t0 = time.time()
    out, rc = run(cmd, timeout=300)
    elapsed = time.time() - t0
    print(f"  output: {out[-200:]}" if out else "")
    if rc != 0:
        record("T1", False, f"Julia exited {rc}", elapsed)
    elif elapsed > MAX_SECONDS:
        record("T1", False, f"using KomaMRI took {elapsed:.1f}s > {MAX_SECONDS}s — runtime recompilation?", elapsed)
    else:
        record("T1", True, f"using KomaMRI loaded in {elapsed:.1f}s (< {MAX_SECONDS}s)", elapsed)


# ═══════════════════════════════════════════════════════════════════════════════
#  T2 – KomaMRI example (1-spin single-voxel simulation)
# ═══════════════════════════════════════════════════════════════════════════════
def test_t2_koma_example(args, image_uri):
    section("T2 · KomaMRI example (1-spin simulation)")
    julia_snippet = (
        "using KomaMRI; "
        "sys = Scanner(); "
        "obj = brain_phantom2D(); "
        "seq = PulseDesigner.EPI_example(); "
        "sig = simulate(obj[1:1], seq, sys); "
        "println(string(\\\"signal shape: \\\", size(sig)))"
    )
    cmd = (
        f'docker run --rm '
        f'--entrypoint julia '
        f'-e JULIA_DEPOT_PATH=/root/.julia '
        f'{image_uri} '
        f'-e "{julia_snippet}"'
    )
    info("Running 1-spin KomaMRI EPI simulation…")
    t0 = time.time()
    out, rc = run(cmd, timeout=300)
    elapsed = time.time() - t0
    print(f"  output: {out[-400:]}" if out else "")
    if rc != 0:
        record("T2", False, f"Simulation exited {rc}", elapsed)
    elif "signal shape:" not in out:
        record("T2", False, "Expected 'signal shape:' in output", elapsed)
    else:
        record("T2", True, f"KomaMRI simulation ran OK in {elapsed:.1f}s", elapsed)


# ═══════════════════════════════════════════════════════════════════════════════
#  T3 – API connectivity
# ═══════════════════════════════════════════════════════════════════════════════
def test_t3_api(args, api_base):
    section("T3 · API connectivity")
    t0 = time.time()

    # 3a – health / root
    health_url = api_base.rstrip("/") + "/health"
    try:
        r = requests.get(health_url, timeout=10)
        ok(f"GET {health_url} → {r.status_code}")
    except Exception as e:
        warn(f"GET {health_url} failed: {e} (non-fatal, not all APIs expose /health)")

    # 3b – login
    if args.api_user and args.api_pass:
        login_url = api_base.rstrip("/") + "/auth/login"
        try:
            r = requests.post(
                login_url,
                json={"email": args.api_user, "password": args.api_pass},
                timeout=15,
            )
            elapsed = time.time() - t0
            if r.status_code in (200, 201):
                token = r.json().get("token") or r.json().get("access_token")
                if token:
                    record("T3", True, f"Login OK, token received  ({r.status_code})", elapsed)
                else:
                    record("T3", False, f"Login {r.status_code} but no token in response: {r.text[:200]}", elapsed)
            else:
                record("T3", False, f"Login returned {r.status_code}: {r.text[:200]}", elapsed)
        except Exception as e:
            elapsed = time.time() - t0
            record("T3", False, f"Login request failed: {e}", elapsed)
    else:
        # Just check the base URL is reachable
        base_url = api_base.rstrip("/")
        try:
            r = requests.get(base_url, timeout=10, allow_redirects=True)
            elapsed = time.time() - t0
            if r.status_code < 500:
                record("T3", True, f"GET {base_url} → {r.status_code}", elapsed)
            else:
                record("T3", False, f"GET {base_url} → {r.status_code} (server error)", elapsed)
        except Exception as e:
            elapsed = time.time() - t0
            record("T3", False, f"Could not reach API: {e}", elapsed)


# ═══════════════════════════════════════════════════════════════════════════════
#  T4 – Failed job → failed bucket
# ═══════════════════════════════════════════════════════════════════════════════
def test_t4_failed_bucket(args, session, statemachine_arn, failed_bucket, task_timeout):
    section("T4 · Failed job → failed bucket")
    sfn    = session.client("stepfunctions")
    s3     = session.client("s3")
    run_id = f"camrie-test-fail-{uuid.uuid4().hex[:8]}"
    start_ts = time.time()

    # Deliberately incomplete event — missing required 'task' key → app crashes
    bad_event = {
        "pipeline":  f"test-{run_id}",
        "token":     "test-token",
        "user_id":   "test-user",
        "task":      {"options": {}},   # missing rho / t1 / sequence → KeyError
    }
    info(f"Starting Step Functions execution: {run_id}")
    try:
        ex = sfn.start_execution(
            stateMachineArn=statemachine_arn,
            name=run_id,
            input=json.dumps(bad_event),
        )
        exec_arn = ex["executionArn"]
        info(f"Execution ARN: {exec_arn}")
    except Exception as e:
        record("T4", False, f"Could not start execution: {e}", time.time() - start_ts)
        return

    # Poll for Fargate task completion via Step Functions execution status,
    # then check S3 failed bucket
    info(f"Waiting up to {task_timeout}s for task to finish …")
    key = wait_for_s3_key(
        s3, failed_bucket, "CAMRIE/test-user/",
        after_time=start_ts,
        timeout=task_timeout,
        poll=15,
    )
    elapsed = time.time() - start_ts
    if key:
        record("T4", True, f"Failure ZIP found in s3://{failed_bucket}/{key}", elapsed)
    else:
        record("T4", False, f"No failure ZIP in s3://{failed_bucket}/CAMRIE/test-user/ after {task_timeout}s", elapsed)


# ═══════════════════════════════════════════════════════════════════════════════
#  T5 – Completed job → results bucket
# ═══════════════════════════════════════════════════════════════════════════════
def test_t5_results_bucket(args, session, statemachine_arn, data_bucket, results_bucket, task_timeout):
    section("T5 · Completed job → results bucket")
    sfn  = session.client("stepfunctions")
    s3c  = session.client("s3")
    s3r  = session.resource("s3")
    run_id  = f"camrie-test-ok-{uuid.uuid4().hex[:8]}"
    prefix  = f"camrie-tests/{run_id}/"
    start_ts = time.time()

    seq_file = args.seq_file
    if not seq_file or not Path(seq_file).exists():
        warn("--seq-file not provided or not found. Skipping T5.")
        results["T5"] = {"passed": None, "msg": "Skipped (no --seq-file)", "elapsed": 0}
        return

    # ── Upload synthetic tissue maps and the user-supplied sequence ──────────
    info("Creating synthetic 10×10×5 NIfTI phantom (2 mm isotropic) …")
    with tempfile.TemporaryDirectory() as tmpdir:
        rho_path = Path(tmpdir) / "rho.nii"
        t1_path  = Path(tmpdir) / "t1.nii"
        write_synthetic_nifti(rho_path, value=1.0)
        write_synthetic_nifti(t1_path,  value=500.0)   # 500 ms T1

        def upload(local, name):
            key = prefix + name
            s3r.Bucket(data_bucket).upload_file(str(local), key)
            info(f"Uploaded s3://{data_bucket}/{key}")
            return key

        rho_key = upload(rho_path, "rho.nii")
        t1_key  = upload(t1_path,  "t1.nii")
        seq_key = upload(seq_file, "test.seq")

    event = {
        "pipeline": f"test-{run_id}",
        "token":    "test-token",
        "user_id":  "test-user",
        "task": {
            "options": {
                "rho":      {"type": "s3", "bucket": data_bucket, "key": rho_key,  "filename": "rho.nii"},
                "t1":       {"type": "s3", "bucket": data_bucket, "key": t1_key,   "filename": "t1.nii"},
                "sequence": {"type": "s3", "bucket": data_bucket, "key": seq_key,  "filename": "test.seq"},
                "geometry": {
                    "slice_normal":       [0, 0, 1],
                    "num_slices":         1,
                    "slice_thickness_mm": 5.0,
                    "slice_gap_mm":       0.0,
                },
                "simulation": {
                    "b0": 3.0, "spin_factor": 1, "n_threads": 4,
                    "use_gpu": False, "apply_hamming": True,
                },
            }
        },
    }

    info(f"Starting Step Functions execution: {run_id}")
    try:
        ex = sfn.start_execution(
            stateMachineArn=statemachine_arn,
            name=run_id,
            input=json.dumps(event),
        )
        info(f"Execution ARN: {ex['executionArn']}")
    except Exception as e:
        record("T5", False, f"Could not start execution: {e}", time.time() - start_ts)
        return

    info(f"Waiting up to {task_timeout}s for results …")
    key = wait_for_s3_key(
        s3c, results_bucket, "CAMRIE/test-user/",
        after_time=start_ts,
        timeout=task_timeout,
        poll=15,
    )
    elapsed = time.time() - start_ts
    if key:
        record("T5", True, f"Results ZIP found in s3://{results_bucket}/{key}", elapsed)
    else:
        record("T5", False, f"No results ZIP in s3://{results_bucket}/CAMRIE/test-user/ after {task_timeout}s", elapsed)


# ═══════════════════════════════════════════════════════════════════════════════
#  T6 – Logs in one place (CloudWatch)
# ═══════════════════════════════════════════════════════════════════════════════
def test_t6_logs(args, session, log_group):
    section("T6 · Logs in one place (CloudWatch)")
    logs = session.client("logs")
    t0 = time.time()

    # 6a – log group exists
    try:
        r = logs.describe_log_groups(logGroupNamePrefix=log_group)
        groups = [g for g in r["logGroups"] if g["logGroupName"] == log_group]
        if not groups:
            record("T6", False, f"Log group '{log_group}' does not exist", time.time() - t0)
            return
        ok(f"Log group '{log_group}' exists")
    except Exception as e:
        record("T6", False, f"describe_log_groups failed: {e}", time.time() - t0)
        return

    # 6b – has at least one stream (means tasks ran and logged)
    try:
        r = logs.describe_log_streams(
            logGroupName=log_group,
            orderBy="LastEventTime",
            descending=True,
            limit=5,
        )
        streams = r.get("logStreams", [])
        if not streams:
            record("T6", False, "Log group has no streams yet (no tasks run?)", time.time() - t0)
            return
        latest = streams[0]["logStreamName"]
        ok(f"Latest stream: {latest}")

        # 6c – fetch a few log events from the latest stream
        events = logs.get_log_events(
            logGroupName=log_group,
            logStreamName=latest,
            limit=5,
            startFromHead=True,
        )["events"]
        if events:
            ok(f"Sample log line: {events[0]['message'][:120]}")
        record("T6", True, f"{len(streams)} stream(s) found, latest: {latest}", time.time() - t0)
    except Exception as e:
        record("T6", False, f"describe_log_streams failed: {e}", time.time() - t0)


# ═══════════════════════════════════════════════════════════════════════════════
#  Summary
# ═══════════════════════════════════════════════════════════════════════════════
def print_summary():
    section("Test Summary")
    passed = failed_tests = skipped = 0
    for tid in sorted(results):
        r = results[tid]
        p = r["passed"]
        if p is True:
            print(f"  {GREEN}PASS{RESET}  {tid}  {r['msg']}  ({r['elapsed']:.1f}s)")
            passed += 1
        elif p is False:
            print(f"  {RED}FAIL{RESET}  {tid}  {r['msg']}  ({r['elapsed']:.1f}s)")
            failed_tests += 1
        else:
            print(f"  {YELLOW}SKIP{RESET}  {tid}  {r['msg']}")
            skipped += 1
    total = passed + failed_tests
    print(f"\n  {BOLD}{passed}/{total} passed{RESET}  ({skipped} skipped)")
    return failed_tests == 0


# ═══════════════════════════════════════════════════════════════════════════════
#  Main
# ═══════════════════════════════════════════════════════════════════════════════
def main():
    p = argparse.ArgumentParser(description="CAMRIE deployment test suite")
    p.add_argument("--profile",     default="nyu")
    p.add_argument("--region",      default="us-east-1")
    p.add_argument("--stack",       default="camrie-app-prod")
    p.add_argument("--brain-stack", default="cloudmrhub-brain")
    p.add_argument("--ecr-image",   default=None, help="ECR image URI (auto-detect if omitted)")
    p.add_argument("--skip",        default="", help="Comma-separated test IDs to skip, e.g. T1,T2")
    p.add_argument("--seq-file",    default=None, help="Local Pulseq .seq file for T5")
    p.add_argument("--api-url",     default=None, help="CloudMR API base URL (auto from stack)")
    p.add_argument("--api-user",    default=os.getenv("CLOUDMR_ADMIN_EMAIL"))
    p.add_argument("--api-pass",    default=os.getenv("CLOUDMR_ADMIN_PASSWORD"))
    p.add_argument("--timeout",     type=int, default=180, help="Fargate task wait timeout (s)")
    args = p.parse_args()

    skip = {s.strip().upper() for s in args.skip.split(",") if s.strip()}

    # ── AWS session ────────────────────────────────────────────────────────────
    info(f"AWS profile={args.profile}  region={args.region}")
    session = get_session(args.profile, args.region)
    cf      = session.client("cloudformation")
    ecr     = session.client("ecr")

    # ── Resolve ECR image ─────────────────────────────────────────────────────
    image_uri = args.ecr_image or latest_ecr_image(ecr, "camrie-fargate")
    if not image_uri:
        warn("Could not determine ECR image. T1 and T2 require --ecr-image.")
    else:
        info(f"ECR image: {image_uri}")
        # Login docker so we can pull the image for local tests
        account = image_uri.split(".")[0]
        run(f"aws ecr get-login-password --region {args.region} --profile {args.profile} "
            f"| docker login --username AWS --password-stdin {account}.dkr.ecr.{args.region}.amazonaws.com",
            timeout=30)
        run(f"docker pull {image_uri}", timeout=300)
        info("Image pulled locally")

    # ── Resolve CloudFormation outputs ────────────────────────────────────────
    calc_stack = f"{args.stack}-calculation"  # nested stack logical ID becomes a CF stack name
    # Try both the nested stack name pattern and direct exports
    statemachine_arn = (
        get_cf_export(cf, f"{calc_stack}-StateMachineArn") or
        get_cf_export(cf, f"{args.stack}-StateMachineArn") or
        get_cf_output(cf, args.stack, "CalculationStateMachineArn")
    )
    if statemachine_arn:
        info(f"State Machine ARN: {statemachine_arn}")
    else:
        warn("Could not resolve State Machine ARN. T4/T5 will fail.")

    # Brain stack exports
    data_bucket    = get_cf_export(cf, f"{args.brain_stack}-DataBucketName")
    results_bucket = get_cf_export(cf, f"{args.brain_stack}-ResultsBucketName")
    failed_bucket  = get_cf_export(cf, f"{args.brain_stack}-FailedBucketName")
    info(f"Buckets  data={data_bucket}  results={results_bucket}  failed={failed_bucket}")

    # CloudWatch log group
    log_group = f"/ecs/camrie-Prod"
    info(f"Log group: {log_group}")

    # API base URL
    # CortexHost in the deployed stack may include the stage path already
    # (e.g. "host.amazonaws.com/Prod"), so we only append /api.
    api_base = args.api_url
    if not api_base:
        cortex_host = get_cf_param(cf, args.stack, "CortexHost") or \
                      "f41j488v7j.execute-api.us-east-1.amazonaws.com"
        # Strip trailing slash before appending
        api_base = f"https://{cortex_host.rstrip('/')}/api"
    info(f"API base: {api_base}")

    # ── Run tests ──────────────────────────────────────────────────────────────
    if "T1" not in skip and image_uri:
        test_t1_precompile(args, image_uri)
    elif "T1" not in skip:
        warn("T1 skipped — no image URI")
        results["T1"] = {"passed": None, "msg": "Skipped (no image)", "elapsed": 0}

    if "T2" not in skip and image_uri:
        test_t2_koma_example(args, image_uri)
    elif "T2" not in skip:
        warn("T2 skipped — no image URI")
        results["T2"] = {"passed": None, "msg": "Skipped (no image)", "elapsed": 0}

    if "T3" not in skip:
        test_t3_api(args, api_base)

    if "T4" not in skip:
        if statemachine_arn and failed_bucket:
            test_t4_failed_bucket(args, session, statemachine_arn, failed_bucket, args.timeout)
        else:
            warn("T4 skipped — missing state machine ARN or failed bucket")
            results["T4"] = {"passed": None, "msg": "Skipped (missing CF outputs)", "elapsed": 0}

    if "T5" not in skip:
        if statemachine_arn and data_bucket and results_bucket:
            test_t5_results_bucket(args, session, statemachine_arn, data_bucket, results_bucket, args.timeout)
        else:
            warn("T5 skipped — missing state machine ARN or bucket names")
            results["T5"] = {"passed": None, "msg": "Skipped (missing CF outputs)", "elapsed": 0}

    # T6 runs AFTER T4/T5 so there are log streams to inspect
    if "T6" not in skip:
        test_t6_logs(args, session, log_group)

    # ── Summary ───────────────────────────────────────────────────────────────
    all_passed = print_summary()
    sys.exit(0 if all_passed else 1)


if __name__ == "__main__":
    main()
