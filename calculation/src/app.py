#!/usr/bin/env python3
"""
CAMRIE Backend – Fargate/Lambda entry point.

Receives a job event (direct payload or S3-trigger), downloads tissue maps
(rho, T1, T2) and a Pulseq sequence file from S3, runs the MRI simulation
pipeline (KomaMRI via Julia), and uploads a zipped result bundle to S3.

Input JSON structure expected in `event["task"]["options"]`:
{
  "rho":      {"type": "s3", "bucket": "...", "key": "...", "filename": "rho.nii.gz"},
  "t1":       {"type": "s3", "bucket": "...", "key": "...", "filename": "t1.nii.gz"},
  "t2":       {"type": "s3", "bucket": "...", "key": "...", "filename": "t2.nii.gz"},  // optional
  "sequence": {"type": "s3", "bucket": "...", "key": "...", "filename": "seq.seq"},
  "geometry": {
    "isocenter_mm":      [0, 0, 0],    // auto-detect from rho if omitted
    "slice_normal":      [0, 0, 1],
    "num_slices":        5,
    "slice_thickness_mm": null,        // read from .seq if omitted
    "slice_gap_mm":      0.0
  },
  "simulation": {
    "b0":          3.0,
    "spin_factor": 1,
    "n_threads":   4,
    "use_gpu":     false,
    "apply_hamming": true
  }
}
"""

import json
import os
import shutil
import sys
import tempfile
import traceback
import uuid
from pathlib import Path
from urllib.parse import urlparse

import boto3
import numpy as np
import requests
from pynico_eros_montin import pynico as pn
from pyable_eros_montin import imaginable as ima
from cmtools import cmaws as ca

import MRI_pipeline as pipeline


# ---------------------------------------------------------------------------
# Logging helper (thin wrapper around pynico Log)
# ---------------------------------------------------------------------------

logger = None


class PrintingLogger(pn.Log):
    def write(self, message, log_type=None, settings=None):
        print(message)
        return super().append(str(message), log_type, settings)


# ---------------------------------------------------------------------------
# JSON helpers
# ---------------------------------------------------------------------------

def sanitize_for_json(data):
    if isinstance(data, dict):
        return {k: sanitize_for_json(v) for k, v in data.items()}
    if isinstance(data, list):
        return [sanitize_for_json(v) for v in data]
    if isinstance(data, (int, float, str, bool, type(None))):
        return data
    return str(data)


def write_json_file(file_path, data):
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(sanitize_for_json(data), f, indent=4)


# ---------------------------------------------------------------------------
# S3 / path utilities
# ---------------------------------------------------------------------------

def pick_random_path(suffix=""):
    return Path(tempfile.gettempdir()) / f"{uuid.uuid4().hex}{suffix}"


def create_random_temp_dir():
    d = pick_random_path()
    d.mkdir()
    return d


def parse_s3_url(url):
    parsed = urlparse(url)
    host = parsed.netloc
    path = parsed.path.lstrip("/")
    if host.startswith("s3.amazonaws.com"):
        parts = path.split("/", 1)
        return parts[1] if len(parts) > 1 else "", parts[0]
    return path, host.split(".")[0]


def download_from_s3(file_info, s3=None):
    """Resolve a file descriptor to a local path.

    Supported descriptor types:
      "local"       – file is already on disk; use local_path directly (no copy)
      "s3"          – download from S3 bucket/key
      "presigned"   – download via presigned GET URL
    """
    filename = file_info["filename"]

    # ── local shortcut (dev / testing) ────────────────────────────────────────
    if file_info.get("type") == "local":
        local_path = file_info.get("local_path", filename)
        if not Path(local_path).exists():
            raise FileNotFoundError(f"local_path not found: {local_path}")
        logger.write(f"Using local file: {local_path}")
        file_info["filename"] = str(local_path)
        return str(local_path)

    local_path = pick_random_path(suffix=Path(filename).suffix)

    if "presigned_url" in file_info:
        logger.write(f"Downloading via presigned URL: {file_info.get('key', filename)}")
        r = requests.get(file_info["presigned_url"])
        r.raise_for_status()
        local_path.write_bytes(r.content)
    else:
        bucket = file_info["bucket"]
        key = file_info["key"]
        if s3 is None:
            s3 = boto3.resource("s3")
        logger.write(f"Downloading s3://{bucket}/{key}")
        s3.Bucket(bucket).download_file(key, str(local_path))

    file_info["filename"] = str(local_path)
    file_info["type"] = "local"
    return str(local_path)


# ---------------------------------------------------------------------------
# Core processing
# ---------------------------------------------------------------------------

def do_process(event, context=None, s3=None):
    global logger

    result_bucket = os.getenv("ResultsBucketName", "camrie-results")
    failed_bucket = os.getenv("FailedBucketName", "camrie-failed")

    logger = PrintingLogger("camrie job", {"event": event, "context": context or {}})

    token = None
    pipelineid = None
    user_id = None
    info_json = {}

    try:
        if s3 is None:
            s3 = boto3.resource("s3")

        # ── 1. Extract payload ──────────────────────────────────────────────
        if "Records" in event and event["Records"] and "s3" in event["Records"][0]:
            rec = event["Records"][0]["s3"]
            bucket_name = rec["bucket"]["name"]
            file_key = rec["object"]["key"]
            logger.write(f"S3 trigger: s3://{bucket_name}/{file_key}")
            fj = pick_random_path(suffix=".json")
            s3.Bucket(bucket_name).download_file(file_key, str(fj))
            with open(fj) as f:
                info_json = json.load(f)
        else:
            logger.write("Direct payload received")
            info_json = event

        pipelineid = info_json.get("pipeline")
        token = info_json.get("token")
        user_id = info_json.get("user_id")
        logger.write(f"pipeline={pipelineid}  user={user_id}")

        # ── 2. Task options ─────────────────────────────────────────────────
        task_info = info_json["task"]
        opts = task_info["options"]

        # ── 3. Download tissue maps + sequence ──────────────────────────────
        rho_info = opts["rho"]
        t1_info  = opts["t1"]
        t2_info  = opts.get("t2")         # optional
        seq_info = opts["sequence"]

        rho_path = download_from_s3(rho_info, s3)
        t1_path  = download_from_s3(t1_info,  s3)
        t2_path  = download_from_s3(t2_info,  s3) if t2_info else None
        seq_path = download_from_s3(seq_info, s3)
        logger.write("All input files downloaded")

        # ── 4. Geometry ─────────────────────────────────────────────────────
        geo = opts.get("geometry", {})
        slice_normal      = geo.get("slice_normal", [0, 0, 1])
        num_slices        = int(geo.get("num_slices", 5))
        slice_thickness   = geo.get("slice_thickness_mm")  # None → read from .seq
        slice_gap         = float(geo.get("slice_gap_mm", 0.0))
        isocenter_mm      = geo.get("isocenter_mm")        # None → auto from rho

        if isocenter_mm is None:
            import SimpleITK as sitk
            rho_img = sitk.ReadImage(rho_path)
            size     = np.array(rho_img.GetSize())
            origin   = np.array(rho_img.GetOrigin())
            spacing  = np.array(rho_img.GetSpacing())
            direction = np.array(rho_img.GetDirection()).reshape(3, 3)
            isocenter_mm = (origin + direction @ ((size - 1) / 2.0 * spacing)).tolist()
            logger.write(f"Auto isocenter: {isocenter_mm}")

        # ── 5. Simulation params ────────────────────────────────────────────
        sim = opts.get("simulation", {})
        b0           = float(sim.get("b0", 3.0))
        spin_factor  = int(sim.get("spin_factor", 1))
        n_threads    = int(sim.get("n_threads", int(os.getenv("CAMRIE_THREADS", "4"))))
        use_gpu      = bool(sim.get("use_gpu", False))
        apply_hamming = bool(sim.get("apply_hamming", True))
        spins_per_voxel  = int(sim.get("spins_per_voxel", 0))
        spin_method      = sim.get("spin_method", pipeline.DEFAULT_SPIN_METHOD)
        parallel_slices  = int(sim.get("parallel_slices", 4))
        slice_padding    = float(sim.get("slice_padding", 1.0))
        t2star_factor    = float(sim.get("t2star_factor", 1.0))

        # ── 6. Output directory ─────────────────────────────────────────────
        out_base = create_random_temp_dir()
        out_dir  = str(out_base / "OUT")
        os.makedirs(out_dir, exist_ok=True)
        logger.write(f"Output dir: {out_dir}")

        # ── 7. Run pipeline ─────────────────────────────────────────────────
        logger.write(f"Starting simulation: {num_slices} slices, B0={b0}T, "
                     f"normal={slice_normal}, spin_factor={spin_factor}")

        volume, series_spec = pipeline.run_pipeline(
            rho_path=rho_path,
            t1_path=t1_path,
            t2_path=t2_path,
            sequence_file=seq_path,
            output_dir=out_dir,
            isocenter_mm=isocenter_mm,
            slice_normal=slice_normal,
            num_slices=num_slices,
            slice_thickness_mm=slice_thickness,
            slice_gap_mm=slice_gap,
            spin_factor=spin_factor,
            b0=b0,
            use_gpu=use_gpu,
            n_threads=n_threads,
            parallel_slices=parallel_slices,
            apply_hamming=apply_hamming,
            spins_per_voxel=spins_per_voxel,
            spin_method=pipeline.normalize_spin_methods(spin_method),
            slice_padding=slice_padding,
            t2star_factor=t2star_factor,
            debug=False,
        )
        logger.write("Pipeline completed successfully")

        # ── 8. Package outputs with cmrOutput ───────────────────────────────
        #
        # ID convention (matches old CAMRIE lambda):
        #   1   – Reconstruction (final 3-D NIfTI volume)
        #  10   – K-space real
        #  11   – K-space imaginary
        #  12   – K-space magnitude
        #
        out = ca.cmrOutput(app="CAMRIE")
        out.setPipeline(pipelineid)
        out.setToken(token)
        out.out["user_id"] = user_id
        out.out["info"] = sanitize_for_json({
            "num_slices":      num_slices,
            "b0":              b0,
            "spin_factor":     spin_factor,
            "spins_per_voxel": spins_per_voxel,
            "parallel_slices": parallel_slices,
            "n_threads":       n_threads,
            "slice_padding":   slice_padding,
            "use_gpu":         use_gpu,
            "apply_hamming":   apply_hamming,
            "slice_normal":    slice_normal,
            "isocenter_mm":    isocenter_mm,
            "slice_thickness_mm": slice_thickness,
            "slice_gap_mm":    slice_gap,
        })

        # Reconstruction volume (id=1)
        recon_path = Path(out_dir) / "reconstruction.nii.gz"
        if recon_path.exists():
            out.addAble(
                ima.Imaginable(str(recon_path)),
                id=1, name="Reconstruction", type="output",
                basename="reconstruction.nii.gz",
            )
            logger.write(f"Added reconstruction: {recon_path}")

        # K-space NIfTIs – freq × phase × slices, 1 coil (ids 10-12)
        for ks_id, ks_suffix, ks_label in [
            (10, "real",      "K-Space Real"),
            (11, "imag",      "K-Space Imaginary"),
            (12, "magnitude", "K-Space Magnitude"),
        ]:
            ks_path = Path(out_dir) / f"kspace_{ks_suffix}.nii.gz"
            if ks_path.exists():
                out.addAble(
                    ima.Imaginable(str(ks_path)),
                    id=ks_id, name=ks_label, type="output",
                    basename=f"kspace_{ks_suffix}.nii.gz",
                )

        # Series geometry JSON + slice PNG previews as auxiliaries
        series_spec_path = Path(out_dir) / "series_spec.json"
        if series_spec_path.exists():
            out.addAuxiliaryFile(str(series_spec_path))
        previews_dir = Path(out_dir) / "previews"
        if previews_dir.is_dir():
            for png in sorted(previews_dir.glob("*.png")):
                out.addAuxiliaryFile(str(png))

        out.setLog(logger)
        out.setOptions(opts)
        out.setTask(task_info)

        # ── 9. Export & upload ──────────────────────────────────────────────
        result_bucket = os.getenv("ResultsBucketName", "camrie-results")
        export_results = out.exportAndZipResultsToS3(
            bucket=result_bucket, deleteoutputzip=True, s3=s3
        )
        logger.write(f"Results uploaded: {export_results}")
        return {"statusCode": 200, "body": json.dumps(export_results)}

    except Exception:
        error_formatted = traceback.format_exc()
        logger.write(error_formatted)

        err_base  = create_random_temp_dir()
        error_dir = err_base / "ERROR_DIR"
        error_dir.mkdir(parents=True, exist_ok=True)

        write_json_file(str(error_dir / "event.json"),   event)
        write_json_file(str(error_dir / "options.json"), info_json)
        (error_dir / "error.txt").write_text(error_formatted)
        # info.json must match the format complete.py expects:
        #   info["headers"]["options"]["pipelineid"] and info["user_id"]
        write_json_file(str(error_dir / "info.json"), {
            "headers": {
                "options": {
                    "pipelineid": pipelineid,
                    "pipeline": pipelineid,
                    "token": token,
                }
            },
            "log": logger.log,
            "user_id": user_id,
        })

        zip_fail_path = Path(
            shutil.make_archive(str(pick_random_path()), "zip", str(error_dir))
        )
        try:
            key = f"CAMRIE/{user_id}/{zip_fail_path.name}"
            s3.Bucket(failed_bucket).upload_file(str(zip_fail_path), key)
            logger.write(f"Failure bundle uploaded to s3://{failed_bucket}/{key}")
        except Exception:
            traceback.print_exc()

        return {"statusCode": 500, "body": json.dumps({"error": error_formatted})}


# ---------------------------------------------------------------------------
# Lambda entry point
# ---------------------------------------------------------------------------

def handler(event, context, s3=None):
    print(f"Received event: {json.dumps(event, indent=2)}")
    return do_process(event, context, s3=s3)


# ---------------------------------------------------------------------------
# Fargate / Step Functions entry point
# ---------------------------------------------------------------------------

def _load_event_from_s3(pointer):
    """Download the real event JSON from S3 when the Lambda stored it there
    to avoid the 8192-char ECS containerOverrides limit."""
    import boto3
    bucket = pointer["s3_event_bucket"]
    key    = pointer["s3_event_key"]
    print(f"Loading event from s3://{bucket}/{key}")
    s3 = boto3.client("s3")
    obj = s3.get_object(Bucket=bucket, Key=key)
    return json.loads(obj["Body"].read().decode("utf-8"))


def main():
    event_str = os.environ.get("FILE_EVENT")
    if not event_str:
        print("No FILE_EVENT provided. Exiting.")
        sys.exit(1)

    try:
        event = json.loads(event_str)
        # If the Lambda stored the real event in S3 (payload too large),
        # FILE_EVENT contains {"s3_event_bucket": ..., "s3_event_key": ...}
        if "s3_event_bucket" in event and "s3_event_key" in event:
            event = _load_event_from_s3(event)
    except Exception as e:
        print(f"Invalid JSON in FILE_EVENT: {e}")
        sys.exit(1)

    result = do_process(event, context=None)
    if result.get("statusCode", 500) != 200:
        print(f"Job failed (statusCode={result['statusCode']}). Exiting with 1.")
        sys.exit(1)

    print("Job succeeded. Exiting with 0.")
    sys.exit(0)


if __name__ == "__main__":
    main()
