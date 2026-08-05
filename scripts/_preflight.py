"""Quick S3 preflight for task.json objects."""
import json, sys
from pathlib import Path

repo = Path(__file__).resolve().parent.parent
task_json = repo / "calculation" / "task.json"

try:
    import boto3
except ImportError:
    print("ERROR: boto3 not installed in this environment")
    sys.exit(1)

profile = sys.argv[1] if len(sys.argv) > 1 else "nyu"
print(f"Using AWS profile: {profile}")

sess = boto3.Session(profile_name=profile, region_name="us-east-1")
s3 = sess.client("s3")

with open(task_json) as f:
    task = json.load(f)

def walk(obj, path="$"):
    if isinstance(obj, dict):
        if obj.get("type") == "s3" and obj.get("bucket") and obj.get("key"):
            yield path, obj
        for k, v in obj.items():
            yield from walk(v, f"{path}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from walk(v, f"{path}[{i}]")

ok = True
for path, desc in walk(task):
    bucket, key = desc["bucket"], desc["key"]
    name = desc.get("filename", key.rsplit("/", 1)[-1])
    try:
        meta = s3.head_object(Bucket=bucket, Key=key)
        size = meta["ContentLength"]
        print(f"  OK  {name}  ({size:,} bytes)  [{path}]")
    except Exception as e:
        print(f"  MISSING  {name}  [{path}]  -> {e}")
        ok = False

sys.exit(0 if ok else 1)
