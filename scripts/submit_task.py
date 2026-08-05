#!/usr/bin/env python3
"""
Submit a task.json directly to the CloudMR Brain API.

Usage:
  python scripts/submit_task.py --task calculation/task_pts.json \
      --api-user you@example.com

  # Re-tail logs for an existing pipeline
  python scripts/submit_task.py --logs-only <pipeline_id>
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

import requests

API_BASE = "https://brain.aws.cloudmrhub.com/Prod/api"


def login(email, password):
    r = requests.post(f"{API_BASE}/auth/login", json={"email": email, "password": password})
    r.raise_for_status()
    d = r.json()
    return d["id_token"], d["user_id"]


def submit(token, task_path):
    task_file = Path(task_path)
    with open(task_file) as f:
        task_doc = json.load(f)

    payload = {
        "cloudapp_name": task_doc.get("task", {}).get("application", "CAMRIE"),
        "alias": task_doc.get("alias", task_file.stem),
        "mode": "mode_1",
        "task": task_doc.get("task", task_doc),
        "output": task_doc.get("output"),
        "computing_unit_id": None,
    }

    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    r = requests.post(f"{API_BASE}/pipeline/queue_job", headers=headers, json=payload)
    try:
        r.raise_for_status()
    except Exception:
        print(f"HTTP {r.status_code}: {r.text}", file=sys.stderr)
        raise
    return r.json()


def _aws_cmd():
    """Find the aws CLI binary — handles WSL (where Windows aws.exe is on PATH as aws.exe)."""
    import shutil
    for candidate in ("aws", "aws.exe"):
        path = shutil.which(candidate)
        if path:
            return path
    raise FileNotFoundError("aws CLI not found on PATH")


def tail_logs(pipeline_id, profile, region, log_group):
    print(f"\nTailing {log_group} for stream containing '{pipeline_id[:16]}'...")
    cmd = [
        _aws_cmd(), "logs", "tail", log_group,
        "--follow",
        "--filter-pattern", pipeline_id[:16],
        "--profile", profile,
        "--region", region,
    ]
    subprocess.run(cmd)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", default="calculation/task_pts.json")
    parser.add_argument("--api-user", default=None)
    parser.add_argument("--token", default=None, help="Skip login — provide id_token directly")
    parser.add_argument("--tail", action="store_true", help="Tail CloudWatch logs after submit")
    parser.add_argument("--log-group", default="/ecs/camrie-Prod")
    parser.add_argument("--aws-profile", default="nyu")
    parser.add_argument("--aws-region", default="us-east-1")
    parser.add_argument("--logs-only", default=None, metavar="PIPELINE_ID",
                        help="Skip submit — just tail logs for an existing pipeline")
    args = parser.parse_args()

    if args.logs_only:
        tail_logs(args.logs_only, args.aws_profile, args.aws_region, args.log_group)
        return

    if args.token:
        token = args.token
    else:
        if not args.api_user:
            print("Provide --token or --api-user", file=sys.stderr)
            sys.exit(1)
        import getpass
        password = getpass.getpass(f"Password for {args.api_user}: ")
        token, _ = login(args.api_user, password)

    print(f"Submitting {args.task} ...")
    result = submit(token, args.task)
    print(json.dumps(result, indent=2))

    pipeline_id = result.get("pipeline", result.get("pipelineId", result.get("id", "")))
    if pipeline_id:
        print(f"\nPipeline ID: {pipeline_id}")
        print(f"\nMonitor:")
        print(f"  aws logs tail {args.log_group} --follow --filter-pattern {pipeline_id[:16]} --profile {args.aws_profile}")
        print(f"  aws batch list-jobs --job-queue camrie-cpu-queue-Prod --job-status RUNNING --profile {args.aws_profile}")
        if args.tail:
            tail_logs(pipeline_id, args.aws_profile, args.aws_region, args.log_group)


if __name__ == "__main__":
    main()
