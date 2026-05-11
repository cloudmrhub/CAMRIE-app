#!/usr/bin/env python3
"""
CAMRIE local runner.

Runs app.py handler directly with the koma conda environment,
not inside Docker.  Results are written to ./local_out/ instead of S3.

Usage
-----
  conda run -n koma python local_test.py [event.json]

The event.json must live alongside this file (or pass a path as arg).
Set  "type": "local"  in the file descriptors and point  "local_path"
to your actual files on disk — no S3 upload needed.

Results land in  calculation/local_out/  (or set LOCAL_RESULTS_DIR env var).
"""

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
    """Drop-in for boto3 Bucket: writes to LOCAL_RESULTS_DIR instead of S3."""
    def __init__(self, name):
        self.name = name

    def upload_file(self, local_path, key):
        dest = Path(LOCAL_RESULTS_DIR) / key
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(local_path, dest)
        print(f"  [local_out] → {dest}")

    def download_file(self, key, dest):
        raise RuntimeError(
            f"S3 download attempted for bucket={self.name} key={key}\n"
            "Use  \"type\": \"local\"  in event.json to skip S3 downloads."
        )


class _LocalS3:
    def Bucket(self, name):
        return _LocalBucket(name)


# ── load event ────────────────────────────────────────────────────────────────
event_path = sys.argv[1] if len(sys.argv) > 1 else str(HERE.parent / "event.json")
print(f"Event : {event_path}")
with open(event_path) as f:
    event = json.load(f)

# ── run ───────────────────────────────────────────────────────────────────────
from app import handler

print("\n" + "═" * 60)
print("Running handler  (local mode)")
print(f"Results dir      : {LOCAL_RESULTS_DIR}")
print("═" * 60 + "\n")

result = handler(event, None, s3=_LocalS3())

# ── report ────────────────────────────────────────────────────────────────────
print("\n" + "═" * 60)
print(f"Status: {result.get('statusCode')}")
try:
    body = json.loads(result.get("body", "{}"))
except Exception:
    body = result.get("body")
print(json.dumps(body, indent=2))
