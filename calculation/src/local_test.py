#!/usr/bin/env python3
"""
CAMRIE local runner.

Runs app.py handler directly with the koma conda environment,
not inside Docker.  Results are written to ./local_out/ instead of S3.

Usage
-----
  # Easiest — use the test runner which also builds the phantom:
  cd calculation && ./run_local_test.sh --seq /path/to/epi.seq

  # Direct call (phantom and event.json must already be ready):
  conda run -n koma python src/local_test.py [event.json]
  conda run -n koma python src/local_test.py task.json --aws-profile nyu

The event.json file lives in  calculation/event.json.
For purely local files, set "type": "local" in the file descriptors and
"local_path" to your actual files on disk. For frontend task JSON that still
points to S3, pass --aws-profile so downloads use your local IAM profile.

Results land in  calculation/local_out/  (or set LOCAL_RESULTS_DIR env var).
"""

import argparse
import json
import os
import sys
import shutil
from pathlib import Path

# ── locate this script so relative imports work from any cwd ──────────────────
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

# ── redirect result / failure upload to local disk ────────────────────────────
LOCAL_RESULTS_DIR = os.environ.get("LOCAL_RESULTS_DIR", str(HERE.parent / "local_out"))
os.environ.setdefault("ResultsBucketName", "__local__")
os.environ.setdefault("FailedBucketName",  "__local__")


class _LocalBucket:
    """Drop-in for boto3 Bucket: local uploads, optional real S3 downloads."""
    def __init__(self, name, download_s3=None):
        self.name = name
        self.download_s3 = download_s3

    def upload_file(self, local_path, key):
        dest = Path(LOCAL_RESULTS_DIR) / key
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(local_path, dest)
        print(f"  [local_out] → {dest}")

    def download_file(self, key, dest):
        if self.download_s3 is not None:
            print(f"  [s3 download] s3://{self.name}/{key} → {dest}")
            Path(dest).parent.mkdir(parents=True, exist_ok=True)
            self.download_s3.Bucket(self.name).download_file(key, dest)
            return
        raise RuntimeError(
            f"S3 download attempted for bucket={self.name} key={key}\n"
            "Use \"type\": \"local\" in the event JSON, or pass --aws-profile "
            "to allow local IAM-backed S3 downloads."
        )


class _LocalS3:
    def __init__(self, download_s3=None):
        self.download_s3 = download_s3

    def Bucket(self, name):
        return _LocalBucket(name, download_s3=self.download_s3)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Run CAMRIE app.py locally, writing result ZIPs to local_out."
    )
    parser.add_argument(
        "event_json",
        nargs="?",
        default=str(HERE.parent / "event.json"),
        help="Event/task JSON to run. Default: calculation/event.json",
    )
    parser.add_argument(
        "--aws-profile",
        default=os.environ.get("CAMRIE_LOCAL_AWS_PROFILE") or os.environ.get("AWS_PROFILE"),
        help="AWS profile used only for S3 downloads. Uploads still go to local_out.",
    )
    parser.add_argument(
        "--aws-region",
        default=os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-east-1",
        help="AWS region for S3 downloads. Default: us-east-1",
    )
    parser.add_argument(
        "--no-preflight",
        action="store_true",
        help="Skip S3 object existence checks before running.",
    )
    return parser.parse_args()


def make_download_s3(profile, region):
    if not profile:
        return None
    try:
        import boto3
    except ImportError as exc:
        raise SystemExit(
            "boto3 is required for --aws-profile S3 downloads. "
            "Install boto3 in the koma environment."
        ) from exc
    session = boto3.Session(profile_name=profile, region_name=region)
    return session.resource("s3")


def iter_s3_descriptors(value, path="$"):
    if isinstance(value, dict):
        if value.get("type") == "file" and isinstance(value.get("options"), dict):
            yield from iter_s3_descriptors(value["options"], f"{path}.options")
            return
        if value.get("type") == "s3" and value.get("bucket") and value.get("key"):
            yield path, value
        for key, item in value.items():
            yield from iter_s3_descriptors(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from iter_s3_descriptors(item, f"{path}[{index}]")


def preflight_s3_objects(event, download_s3):
    if download_s3 is None:
        return
    missing = []
    checked = 0
    client = download_s3.meta.client
    print("\nS3 preflight")
    for path, desc in iter_s3_descriptors(event):
        checked += 1
        bucket = desc["bucket"]
        key = desc["key"]
        label = desc.get("filename", key.rsplit("/", 1)[-1])
        try:
            client.head_object(Bucket=bucket, Key=key)
            print(f"  OK      {label}")
        except Exception as exc:
            missing.append((path, bucket, key, label, exc))
            print(f"  MISSING {label}")

    if missing:
        print("\nMissing S3 objects:")
        for path, bucket, key, label, exc in missing:
            print(f"  - {label}")
            print(f"    path: {path}")
            print(f"    s3:   s3://{bucket}/{key}")
            print(f"    err:  {exc}")
        raise SystemExit(f"S3 preflight failed: {len(missing)} missing object(s)")
    print(f"  Checked {checked} S3 object(s)")


# ── load event ────────────────────────────────────────────────────────────────
args = parse_args()
event_path = args.event_json
print(f"Event : {event_path}")
with open(event_path) as f:
    event = json.load(f)

# ── run ───────────────────────────────────────────────────────────────────────
from app import handler

print("\n" + "═" * 60)
print("Running handler  (local mode)")
print(f"Results dir      : {LOCAL_RESULTS_DIR}")
if args.aws_profile:
    print(f"S3 downloads     : profile={args.aws_profile} region={args.aws_region}")
else:
    print("S3 downloads     : disabled")
print("═" * 60 + "\n")

download_s3 = make_download_s3(args.aws_profile, args.aws_region)
if not args.no_preflight:
    preflight_s3_objects(event, download_s3)

result = handler(event, None, s3=_LocalS3(download_s3))

# ── report ────────────────────────────────────────────────────────────────────
print("\n" + "═" * 60)
print(f"Status: {result.get('statusCode')}")
try:
    body = json.loads(result.get("body", "{}"))
except Exception:
    body = result.get("body")
print(json.dumps(body, indent=2))
