#!/usr/bin/env python3
"""
List all CAMRIE pipelines from DynamoDB (CloudMR-Pipelines table).
Uses your AWS IAM profile — no email/password needed.

Usage:
    python scripts/debug-query-pipelines.py                   # all pipelines
    python scripts/debug-query-pipelines.py --failed-only     # only failed
    python scripts/debug-query-pipelines.py --last 10         # last 10
    python scripts/debug-query-pipelines.py --profile nyu     # explicit profile
"""

import argparse
import json
import os
import sys
from pathlib import Path

import boto3

SCRIPT_DIR    = Path(__file__).parent
OUTPUT_FILE   = SCRIPT_DIR / "debug-failed-tasks.json"
AWS_REGION    = "us-east-1"
PIPELINES_TABLE = "CloudMR-Pipelines"
CAMRIE_APP_IDS  = None  # auto-detect


def scan_pipelines(ddb, cloudapp_ids=None):
    """Scan all pipelines. Optionally filter by cloudapp_id set."""
    items = []
    kwargs = {"TableName": PIPELINES_TABLE}
    while True:
        resp = ddb.scan(**kwargs)
        items.extend(resp.get("Items", []))
        if "LastEvaluatedKey" not in resp:
            break
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]

    # Parse DynamoDB items to plain dicts
    pipelines = []
    for item in items:
        entry = {}
        for k, v in item.items():
            entry[k] = list(v.values())[0]
        pipelines.append(entry)

    # Filter to CAMRIE if cloudapp_ids provided
    if cloudapp_ids:
        pipelines = [p for p in pipelines if p.get("cloudapp_id") in cloudapp_ids]

    return pipelines


def get_camrie_app_ids(ddb):
    """Find CAMRIE cloudapp_id(s) from CloudMR-CloudApps table."""
    ids = set()
    try:
        resp = ddb.scan(TableName="CloudMR-CloudApps")
        for item in resp.get("Items", []):
            parsed = {k: list(v.values())[0] for k, v in item.items()}
            if "camrie" in parsed.get("name", "").lower():
                app_id = parsed.get("appId") or parsed.get("id") or parsed.get("cloudapp_id")
                if app_id:
                    ids.add(app_id)
    except Exception:
        pass
    return ids or None


def main():
    parser = argparse.ArgumentParser(description="List CAMRIE pipelines from DynamoDB")
    parser.add_argument("--profile", default=os.getenv("AWS_PROFILE", "nyu"),
                        help="AWS IAM profile (default: nyu)")
    parser.add_argument("--region",  default=AWS_REGION)
    parser.add_argument("--failed-only", "-f", action="store_true")
    parser.add_argument("--last", "-n", type=int, help="Show last N")
    parser.add_argument("--all-apps", action="store_true",
                        help="Show all apps, not just CAMRIE")
    args = parser.parse_args()

    sess = boto3.Session(profile_name=args.profile, region_name=args.region)
    ddb  = sess.client("dynamodb")

    # Find CAMRIE app IDs
    cloudapp_ids = None if args.all_apps else get_camrie_app_ids(ddb)
    if cloudapp_ids:
        print(f"[OK] CAMRIE app ID(s): {cloudapp_ids}")

    # Scan pipelines
    print(f"[INFO] Scanning {PIPELINES_TABLE}...")
    pipelines = scan_pipelines(ddb, cloudapp_ids)
    print(f"[OK] {len(pipelines)} pipelines found")

    # Sort newest first
    pipelines.sort(key=lambda x: x.get("created_at", ""), reverse=True)

    # Filter
    failed = [p for p in pipelines if "fail" in p.get("status", "").lower()
              or "error" in p.get("status", "").lower()
              or "timeout" in p.get("status", "").lower()]

    # Status breakdown
    statuses = {}
    for p in pipelines:
        s = p.get("status", "unknown")
        statuses[s] = statuses.get(s, 0) + 1

    print(f"\n{'='*80}")
    print(f"  CAMRIE Pipelines — Total: {len(pipelines)}  Failed: {len(failed)}")
    print(f"{'='*80}")
    for s, cnt in sorted(statuses.items(), key=lambda x: -x[1]):
        print(f"  {s}: {cnt}")

    # Table
    display = failed if args.failed_only else pipelines
    if args.last:
        display = display[:args.last]

    print(f"\n  {'#':<4} {'STATUS':<12} {'ALIAS':<25} {'CREATED':<22} {'PIPELINE ID'}")
    print(f"  {'─'*4} {'─'*12} {'─'*25} {'─'*22} {'─'*36}")
    for i, p in enumerate(display):
        print(f"  {i:<4} {p.get('status','?'):<12} "
              f"{(p.get('alias','—') or '—')[:25]:<25} "
              f"{p.get('created_at','')[:19]:<22} "
              f"{p.get('pipeline','')}")

    # Write JSON
    output = {
        "profile": args.profile,
        "total":   len(pipelines),
        "failed":  len(failed),
        "failed_tasks": failed,
        "all_tasks":    pipelines,
    }
    with open(OUTPUT_FILE, "w") as f:
        json.dump(output, f, indent=2, default=str)

    print(f"\n[OK] Saved to {OUTPUT_FILE}")
    print(f"\nNext:")
    print(f"  python scripts/debug-failed-task.py -p <pipeline_id>")
    print(f"  python scripts/debug-failed-task.py -i 0")


if __name__ == "__main__":
    main()
