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
import sys
import time
from pathlib import Path

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


def poll_pipeline(base_url, token, pipeline_id, timeout=600, interval=15):
    """Poll pipeline status until completed, failed, or timeout."""
    headers = {"Authorization": f"Bearer {token}"}
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            r = requests.get(f"{base_url}/pipeline/{pipeline_id}", headers=headers)
            if r.status_code == 200:
                data = r.json()
                # API may return a list, dict, bool, or other types
                if isinstance(data, list):
                    data = data[0] if data else {}
                if not isinstance(data, dict):
                    elapsed = time.time() - t0
                    info(f"[{elapsed:.0f}s] Unexpected response type ({type(data).__name__}): {data}")
                    time.sleep(interval)
                    continue
                status = data.get("status", "unknown")
                elapsed = time.time() - t0
                info(f"[{elapsed:.0f}s] Pipeline status: {status}")
                if status in ("completed", "failed"):
                    return data
            else:
                elapsed = time.time() - t0
                info(f"[{elapsed:.0f}s] HTTP {r.status_code} — retrying…")
        except Exception as e:
            elapsed = time.time() - t0
            info(f"[{elapsed:.0f}s] Poll error: {e} — retrying…")
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
    parser.add_argument("--seq-file", required=True, help="Path to Pulseq .seq file")
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

    args = parser.parse_args()
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
    ok(f"Job queued!")
    info(f"Pipeline:      {pipeline_id}")
    info(f"Execution ARN: {result.get('executionArn')}")
    if result.get("computingUnit"):
        cu = result["computingUnit"]
        info(f"Computing Unit: {cu.get('alias')} ({cu.get('mode')}) id={cu.get('id')}")

    if args.no_poll:
        print(f"\n  Use pipeline ID to check status:")
        print(f"  GET {args.api_base}/pipeline/{pipeline_id}")
        return

    # ── Step 4: Poll ───────────────────────────────────────────────────────────
    print(f"\n═══ Step 4: Polling (timeout={args.timeout}s) ═══")
    final = poll_pipeline(args.api_base, token, pipeline_id,
                          timeout=args.timeout, interval=15)
    if final:
        status = final.get("status", "unknown")
        if status == "completed":
            ok(f"Simulation completed!")
            if final.get("results"):
                info(f"Results: {json.dumps(final['results'], indent=2)}")
        else:
            fail(f"Simulation ended with status: {status}")
            if final.get("log"):
                info(f"Log: {final['log']}")
    print()


if __name__ == "__main__":
    main()
