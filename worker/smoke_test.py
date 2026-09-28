#!/usr/bin/env python3
"""
End-to-end smoke test for a deployed CAMRIE Mode 2 worker.

Submits a small simulation exactly the way CloudMR Brain does for a mode_2
computing unit: every input is a presigned GET URL and the result goes back
through a presigned PUT URL. The test hosts inputs and the result in the
worker stack's OWN results bucket, so it touches nothing in the CloudMRHub
account and does not need Brain credentials.

Usage (from the repo root):
    python worker/smoke_test.py --profile eros
    python worker/smoke_test.py --profile eros --sequence data/sequences/T1-Weighted_Spin_Echo.seq

Reads the endpoint and API key from ~/.camrie/config.toml (written by
`manage.py deploy`). Exit code 0 only if a result ZIP arrives.
"""
import argparse
import json
import sys
import time
import uuid
import zipfile
from pathlib import Path

import boto3
import requests
from botocore.config import Config

sys.path.insert(0, str(Path(__file__).resolve().parent))
import manage  # noqa: E402  (reuse config + stack lookup)

REPO = Path(__file__).resolve().parents[1]
PHANTOM = REPO / "calculation" / "phantom"
EXPIRES = 3 * 3600


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--profile", default=None)
    ap.add_argument("--region", default=None)
    ap.add_argument("--sequence",
                    default=str(REPO / "data" / "sequences" / "PD-Weighted_Spin_Echo.seq"))
    ap.add_argument("--timeout", type=int, default=3600, help="seconds to wait")
    ap.add_argument("--keep", action="store_true", help="do not delete test objects")
    args = ap.parse_args()

    cfg = manage.load_config()
    profile = args.profile or cfg.get("profile", "default")
    region = args.region or cfg.get("region", manage.DEFAULT_REGION)
    endpoint, api_key = cfg.get("endpoint"), cfg.get("api_key")
    if not endpoint or not api_key:
        sys.exit("No endpoint/api_key in ~/.camrie/config.toml - run manage.py deploy first")

    session = manage.get_session(profile, region)
    status, outputs = manage.get_stack_outputs(session)
    if not status:
        sys.exit(f"Stack {manage.STACK_NAME} not found for profile {profile}")
    bucket = outputs["ResultsBucketName"]
    cluster = outputs["ClusterName"]

    # Regional endpoint + SigV4 so presigned URLs work without redirects.
    s3 = session.client("s3", config=Config(signature_version="s3v4"),
                        endpoint_url=f"https://s3.{region}.amazonaws.com")
    run = f"smoke/{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
    print(f"Account  {session.client('sts').get_caller_identity()['Account']} ({profile})")
    print(f"Endpoint {endpoint}")
    print(f"Bucket   s3://{bucket}/{run}/")

    seq = Path(args.sequence)
    inputs = {"rho": PHANTOM / "rho.nii", "t1": PHANTOM / "t1.nii",
              "t2": PHANTOM / "t2.nii", "sequence": seq}
    for p in inputs.values():
        if not p.exists():
            sys.exit(f"missing input: {p}")

    descriptors = {}
    for role, path in inputs.items():
        key = f"{run}/inputs/{path.name}"
        s3.upload_file(str(path), bucket, key)
        url = s3.generate_presigned_url("get_object", Params={"Bucket": bucket, "Key": key},
                                        ExpiresIn=EXPIRES)
        descriptors[role] = {"type": "presigned", "presigned_url": url,
                             "filename": path.name, "key": key}
    print(f"Uploaded {len(inputs)} inputs, presigned GET URLs generated")

    result_key = f"{run}/result.zip"
    failed_key = f"{run}/failed.zip"
    put = lambda k: s3.generate_presigned_url(  # noqa: E731
        "put_object", Params={"Bucket": bucket, "Key": k}, ExpiresIn=EXPIRES)

    pipeline = f"mode2-smoke-{uuid.uuid4()}"
    job = {
        "pipeline": pipeline,
        "token": "mode2-smoke",
        "user_id": "mode2-smoke",
        "presigned_upload_url": put(result_key),
        "presigned_failed_upload_url": put(failed_key),
        "task": {"options": {
            **descriptors,
            "geometry": {"isocenter_mm": None, "slice_normal": [0, 0, 1],
                         "num_slices": 1, "slice_thickness_mm": None, "slice_gap_mm": 0.0},
            "simulation": {"b0": 1.5, "spin_factor": 1, "n_threads": 4, "use_gpu": False,
                           "apply_hamming": True, "spins_per_voxel": 0,
                           "parallel_slices": 1, "slice_padding": 0.5},
        }},
    }
    print(f"Job payload: {len(json.dumps(job))} chars "
          f"({'inline' if len(json.dumps(job)) <= 7000 else 'via S3 pointer'})")

    r = requests.post(f"{endpoint}/compute", json=job, timeout=60,
                      headers={"X-API-Key": api_key})
    print(f"POST /compute -> HTTP {r.status_code} {r.text[:300]}")
    if r.status_code != 202:
        sys.exit(1)
    task_arn = r.json()["task_arn"]

    ecs = session.client("ecs")
    t0, last, rc = time.time(), None, 1
    while time.time() - t0 < args.timeout:
        t = ecs.describe_tasks(cluster=cluster, tasks=[task_arn])["tasks"][0]
        st = t["lastStatus"]
        if st != last:
            print(f"  [{time.time() - t0:6.0f}s] task {st}")
            last = st
        if st == "STOPPED":
            c = t["containers"][0]
            print(f"  exit={c.get('exitCode')} stopCode={t.get('stopCode')} "
                  f"reason={t.get('stoppedReason')} {c.get('reason', '')}")
            break
        time.sleep(15)
    else:
        print("  TIMEOUT waiting for the task")

    for key, label in ((result_key, "RESULT"), (failed_key, "FAILURE BUNDLE")):
        try:
            head = s3.head_object(Bucket=bucket, Key=key)
        except s3.exceptions.ClientError:
            continue
        local = Path(__file__).parent / f"_smoke_{label.split()[0].lower()}.zip"
        s3.download_file(bucket, key, str(local))
        print(f"\n{label} arrived via presigned PUT: s3://{bucket}/{key} "
              f"({head['ContentLength'] / 1e6:.2f} MB)")
        with zipfile.ZipFile(local) as z:
            for n in z.namelist():
                print(f"   {n}")
            if label == "RESULT":
                info = json.loads(z.read("info.json"))
                # Brain's complete handler matches results to jobs via
                # info.json -> headers.options.pipelineid (set by cmrOutput.setPipeline)
                got = ((info.get("headers") or {}).get("options") or {}).get("pipelineid")
                print(f"   info.json headers.options.pipelineid = {got!r}")
                print(f"   submitted pipeline                   = {pipeline!r}")
                has_kspace = any("kspace" in n for n in z.namelist())
                has_recon = any("reconstruction" in n for n in z.namelist())
                ok_id = got == pipeline
                print(f"   kspace={has_kspace} reconstruction={has_recon} pipeline_match={ok_id}")
                rc = 0 if (has_kspace and has_recon and ok_id) else 1
            else:
                err = next((n for n in z.namelist() if n.endswith("error.txt")), None)
                if err:
                    print("   --- error.txt (tail) ---")
                    print("\n".join(z.read(err).decode(errors="replace").splitlines()[-15:]))
        local.unlink()

    if not args.keep:
        for obj in s3.list_objects_v2(Bucket=bucket, Prefix=run).get("Contents", []):
            s3.delete_object(Bucket=bucket, Key=obj["Key"])
        print(f"\nCleaned up s3://{bucket}/{run}/")

    print("\nSMOKE TEST", "PASSED" if rc == 0 else "FAILED")
    sys.exit(rc)


if __name__ == "__main__":
    main()
