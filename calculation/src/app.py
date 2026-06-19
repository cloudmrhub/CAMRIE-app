#!/usr/bin/env python3
"""
CAMRIE Backend – Fargate/Lambda entry point.

Receives a job event (direct payload or S3-trigger), downloads tissue maps
(rho, T1, T2) and one or more sequence files from S3, runs the MRI simulation
pipeline (KomaMRI via Julia), and uploads one zipped result bundle to S3.

The entry points stay stable for local and Batch development:
  handler(event, context, s3=None)
  do_process(event, context=None, s3=None)

Accepted payloads:
  1. Legacy/internal form with direct rho/t1/t2 + sequence descriptors.
  2. Frontend form with bodymodel ZIP + task.options.sequences[].

For frontend payloads, bodymodel ZIP extraction happens inside this Batch
container. The ZIP is expected to include an info.json that points to rho/PD,
T1, and T2 files. This keeps the router Lambda lightweight and makes CPU and
GPU Batch jobs behave identically.

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

import copy
import json
import os
import re
import shutil
import sys
import tempfile
import traceback
import uuid
import zipfile
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


def deep_merge(base, override):
    """Recursively merge two JSON-like dicts without mutating either one."""
    result = copy.deepcopy(base or {})
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def truthy(value):
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "y", "on")


def safe_slug(value, default="item"):
    text = str(value or default)
    text = re.sub(r"[^A-Za-z0-9_.-]+", "_", text).strip("._-")
    return text[:80] or default


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


def unwrap_file_descriptor(file_info):
    """Accept both internal descriptors and frontend {type:file, options:{...}}."""
    if file_info is None:
        return None
    if not isinstance(file_info, dict):
        raise TypeError(f"File descriptor must be a dict, got {type(file_info).__name__}")
    if file_info.get("type") == "file" and isinstance(file_info.get("options"), dict):
        desc = copy.deepcopy(file_info["options"])
        if file_info.get("id") is not None:
            desc.setdefault("id", file_info["id"])
        return desc
    return copy.deepcopy(file_info)


def as_cmr_file_descriptor(file_info):
    """Convert internal/local descriptors into cloudmr-tools getCMRFile shape."""
    desc = unwrap_file_descriptor(file_info)
    if desc.get("type") == "local" and "filename" not in desc and desc.get("local_path"):
        desc["filename"] = desc["local_path"]
    return {"type": "file", "id": desc.get("id", -1), "options": desc}


def download_from_s3(file_info, s3=None):
    """Resolve a file descriptor to a local path.

    Supported descriptor types:
      "local"       – file is already on disk; use local_path directly (no copy)
      "s3"          – download from S3 bucket/key
      "presigned"   – download via presigned GET URL
    """
    file_info = unwrap_file_descriptor(file_info)
    filename = (
        file_info.get("filename")
        or Path(file_info.get("local_path") or file_info.get("key") or "input").name
    )

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
        logger.write(f"Downloading s3://{bucket}/{key}")
        try:
            return ca.getCMRFile(as_cmr_file_descriptor(file_info), s3=s3)
        except Exception as exc:
            raise RuntimeError(
                f"Could not download s3://{bucket}/{key}. "
                "Check that the frontend file descriptor points to an existing object."
            ) from exc

    file_info["filename"] = str(local_path)
    file_info["type"] = "local"
    return str(local_path)


def safe_extract_zip(zip_path, out_dir):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as zf:
        for member in zf.infolist():
            target = (out_dir / member.filename).resolve()
            try:
                target.relative_to(out_dir.resolve())
            except ValueError:
                raise ValueError(f"Unsafe path inside bodymodel ZIP: {member.filename}")
            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(member) as src, open(target, "wb") as dst:
                    shutil.copyfileobj(src, dst)
    return str(out_dir)


def find_info_json(root_dir):
    for path in Path(root_dir).rglob("*"):
        if path.is_file() and path.name.lower() == "info.json":
            return path
    return None


def normalize_key(value):
    return re.sub(r"[^a-z0-9]+", "", str(value).lower())


BODYMODEL_ALIASES = {
    "rho": {"rho", "rhoh", "pd", "protondensity", "protondensitymap", "density"},
    "t1": {"t1", "t1map", "t1ms", "longitudinalrelaxation"},
    "t2": {"t2", "t2map", "t2ms", "transverserelaxation"},
}


def candidate_strings(value):
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        preferred = []
        for key in ("local_path", "path", "filepath", "filename", "file", "name", "key"):
            item = value.get(key)
            if isinstance(item, str):
                preferred.append(item)
        for item in value.values():
            preferred.extend(candidate_strings(item))
        return preferred
    if isinstance(value, list):
        found = []
        for item in value:
            found.extend(candidate_strings(item))
        return found
    return []


def candidates_from_info(info, aliases):
    found = []

    def walk(value, key_hint=None):
        if normalize_key(key_hint or "") in aliases:
            found.extend(candidate_strings(value))

        if isinstance(value, dict):
            role_values = [
                value.get(k)
                for k in ("role", "type", "name", "label", "modality", "contrast")
            ]
            if any(normalize_key(v) in aliases for v in role_values if v is not None):
                found.extend(candidate_strings(value))
            for key, item in value.items():
                walk(item, key)
        elif isinstance(value, list):
            for item in value:
                walk(item, key_hint)

    walk(info)
    return found


def resolve_relative_path(root_dir, value):
    if not value or not isinstance(value, str):
        return None
    candidate = Path(value)
    if candidate.is_absolute() and candidate.exists():
        return candidate
    candidate = Path(root_dir) / value
    if candidate.exists():
        return candidate
    matches = list(Path(root_dir).rglob(Path(value).name))
    return matches[0] if matches else None


def scan_bodymodel_for_map(root_dir, role):
    aliases = BODYMODEL_ALIASES[role]
    extensions = (".nii", ".nii.gz", ".mha", ".mhd", ".nrrd")
    candidates = []
    for path in Path(root_dir).rglob("*"):
        if not path.is_file():
            continue
        lower_name = path.name.lower()
        if not any(lower_name.endswith(ext) for ext in extensions):
            continue
        normalized = normalize_key(path.name.replace(".nii.gz", "").replace(".nii", ""))
        if any(alias in normalized for alias in aliases):
            candidates.append(path)
    candidates.sort(key=lambda p: (len(p.name), str(p)))
    return candidates[0] if candidates else None


def resolve_bodymodel_map(root_dir, info, role, required=True):
    aliases = BODYMODEL_ALIASES[role]
    for value in candidates_from_info(info, aliases):
        path = resolve_relative_path(root_dir, value)
        if path:
            return str(path)
    path = scan_bodymodel_for_map(root_dir, role)
    if path:
        return str(path)
    if required:
        raise FileNotFoundError(
            f"Could not resolve {role} map from bodymodel info.json or extracted files"
        )
    return None


def prepare_bodymodel(bodymodel_info, s3):
    local_path = Path(download_from_s3(bodymodel_info, s3))
    if local_path.is_dir():
        root_dir = local_path
    else:
        if not zipfile.is_zipfile(local_path):
            raise ValueError(f"Bodymodel must be a ZIP or directory: {local_path}")
        root_dir = create_random_temp_dir() / "bodymodel"
        logger.write(f"Extracting bodymodel ZIP: {local_path}")
        safe_extract_zip(local_path, root_dir)

    info_path = find_info_json(root_dir)
    info = {}
    if info_path:
        logger.write(f"Bodymodel info.json: {info_path}")
        with open(info_path, encoding="utf-8") as f:
            info = json.load(f)
    else:
        logger.write("Bodymodel info.json not found; falling back to filename scan")

    return {
        "root_dir": str(root_dir),
        "info_path": str(info_path) if info_path else None,
        "info": info,
        "rho": resolve_bodymodel_map(root_dir, info, "rho", required=True),
        "t1": resolve_bodymodel_map(root_dir, info, "t1", required=True),
        "t2": resolve_bodymodel_map(root_dir, info, "t2", required=False),
    }


def resolve_tissue_maps(opts, s3):
    if "rho" in opts and "t1" in opts:
        return {
            "source": "direct",
            "bodymodel": None,
            "rho": download_from_s3(opts["rho"], s3),
            "t1": download_from_s3(opts["t1"], s3),
            "t2": download_from_s3(opts.get("t2"), s3) if opts.get("t2") else None,
        }

    bodymodel = opts.get("bodymodel")
    if not bodymodel:
        raise KeyError("Task options must include either rho/t1/t2 or bodymodel")

    body = prepare_bodymodel(bodymodel, s3)
    logger.write(
        "Resolved bodymodel maps: "
        f"rho={body['rho']} t1={body['t1']} t2={body.get('t2')}"
    )
    return {
        "source": "bodymodel",
        "bodymodel": body,
        "rho": body["rho"],
        "t1": body["t1"],
        "t2": body.get("t2"),
    }


def normalize_vector(value, default):
    if not value:
        return default
    arr = np.array(value, dtype=np.float64)
    norm = np.linalg.norm(arr)
    if norm == 0:
        return default
    return (arr / norm).tolist()


def infer_slice_normal(geo):
    if geo.get("slice_normal") is not None:
        return normalize_vector(geo["slice_normal"], [0, 0, 1])

    affine = geo.get("affine")
    if isinstance(affine, list) and len(affine) >= 3:
        try:
            normal = [affine[0][2], affine[1][2], affine[2][2]]
            return normalize_vector(normal, [0, 0, 1])
        except Exception:
            pass

    orientation = str(geo.get("ui", {}).get("orientation", "")).lower()
    if orientation.startswith("sag"):
        return [1, 0, 0]
    if orientation.startswith("cor"):
        return [0, 1, 0]
    return [0, 0, 1]


def normalize_geometry(geo):
    geo = copy.deepcopy(geo or {})
    slice_info = geo.get("slice", {}) if isinstance(geo.get("slice"), dict) else {}
    return {
        "isocenter_mm": geo.get("isocenter_mm"),
        "slice_normal": infer_slice_normal(geo),
        "num_slices": int(geo.get("num_slices", slice_info.get("num_slices", 5))),
        "slice_thickness_mm": geo.get("slice_thickness_mm", slice_info.get("thickness_mm")),
        "slice_gap_mm": float(geo.get("slice_gap_mm", slice_info.get("gap_mm", 0.0))),
        "fov_mm": geo.get("fov_mm"),
        "seq_fov_mm": geo.get("seq_fov_mm", geo.get("fov_mm")),
        "matrix": geo.get("matrix"),
    }


def normalize_simulation(opts, sequence_spec):
    marie_inputs = opts.get("marie_inputs", {}) if isinstance(opts.get("marie_inputs"), dict) else {}
    base = copy.deepcopy(opts.get("simulation", {}) or {})
    if "b0" not in base and marie_inputs.get("b0") is not None:
        base["b0"] = marie_inputs["b0"]
    sim = deep_merge(base, sequence_spec.get("simulation", {}) or {})
    return {
        "b0": float(sim.get("b0", 3.0)),
        "spin_factor": int(sim.get("spin_factor", 1)),
        "n_threads": int(sim.get("n_threads", int(os.getenv("CAMRIE_THREADS", "4")))),
        "use_gpu": truthy(sim.get("use_gpu", False)),
        "apply_hamming": truthy(sim.get("apply_hamming", True)),
        "spins_per_voxel": int(sim.get("spins_per_voxel", 0)),
        "spin_method": sim.get("spin_method", pipeline.DEFAULT_SPIN_METHOD),
        "parallel_slices": int(sim.get("parallel_slices", 4)),
        "slice_padding": float(sim.get("slice_padding", 1.0)),
        "t2star_factor": float(sim.get("t2star_factor", 1.0)),
    }


def sequence_descriptor_from_spec(sequence_spec):
    return (
        sequence_spec.get("sequence")
        or sequence_spec.get("file")
        or sequence_spec.get("options")
        or sequence_spec
    )


def sequence_name(index, sequence_spec, descriptor):
    desc = unwrap_file_descriptor(descriptor)
    raw = (
        sequence_spec.get("name")
        or sequence_spec.get("alias")
        or desc.get("filename")
        or desc.get("key")
        or f"sequence_{index:03d}"
    )
    return Path(str(raw)).name


def normalize_sequence_jobs(opts):
    raw_sequences = opts.get("sequences")
    if raw_sequences:
        jobs = []
        for index, sequence_spec in enumerate(raw_sequences, start=1):
            descriptor = sequence_descriptor_from_spec(sequence_spec)
            name = sequence_name(index, sequence_spec, descriptor)
            jobs.append({
                "index": index,
                "name": name,
                "slug": safe_slug(f"seq{index:03d}_{Path(name).stem}"),
                "descriptor": descriptor,
                "geometry": normalize_geometry(
                    deep_merge(opts.get("geometry", {}) or {}, sequence_spec.get("geometry", {}) or {})
                ),
                "simulation": normalize_simulation(opts, sequence_spec),
                "raw": sequence_spec,
            })
        return jobs

    descriptor = opts["sequence"]
    name = sequence_name(1, {}, descriptor)
    return [{
        "index": 1,
        "name": name,
        "slug": safe_slug(Path(name).stem or "sequence_001"),
        "descriptor": descriptor,
        "geometry": normalize_geometry(opts.get("geometry", {}) or {}),
        "simulation": normalize_simulation(opts, {}),
        "raw": {"sequence": descriptor},
    }]


def compute_auto_isocenter(rho_path):
    import SimpleITK as sitk
    rho_img = sitk.ReadImage(rho_path)
    size = np.array(rho_img.GetSize())
    origin = np.array(rho_img.GetOrigin())
    spacing = np.array(rho_img.GetSpacing())
    direction = np.array(rho_img.GetDirection()).reshape(3, 3)
    return (origin + direction @ ((size - 1) / 2.0 * spacing)).tolist()


def add_auxiliary_file(out, src, aux_dir, basename=None):
    if basename is None:
        out.addAuxiliaryFile(str(src))
        return
    aux_dir = Path(aux_dir)
    aux_dir.mkdir(parents=True, exist_ok=True)
    dest = aux_dir / basename
    shutil.copy2(src, dest)
    out.addAuxiliaryFile(str(dest))


def add_sequence_outputs(out, out_dir, job, multi_sequence, aux_dir):
    out_path = Path(out_dir)
    slug = job["slug"]
    id_base = 0 if not multi_sequence else job["index"] * 100
    label_prefix = "" if not multi_sequence else f"{job['name']} "
    basename_prefix = "" if not multi_sequence else f"{slug}_"

    recon_path = out_path / "reconstruction.nii.gz"
    if recon_path.exists():
        out.addAble(
            ima.Imaginable(str(recon_path)),
            id=id_base + 1,
            name=f"{label_prefix}Reconstruction".strip(),
            type="output",
            basename=f"{basename_prefix}reconstruction.nii.gz",
        )
        logger.write(f"Added reconstruction: {recon_path}")

    for ks_id, ks_suffix, ks_label in [
        (10, "real", "K-Space Real"),
        (11, "imag", "K-Space Imaginary"),
        (12, "magnitude", "K-Space Magnitude"),
    ]:
        ks_path = out_path / f"kspace_{ks_suffix}.nii.gz"
        if ks_path.exists():
            out.addAble(
                ima.Imaginable(str(ks_path)),
                id=id_base + ks_id,
                name=f"{label_prefix}{ks_label}".strip(),
                type="output",
                basename=f"{basename_prefix}kspace_{ks_suffix}.nii.gz",
            )

    series_spec_path = out_path / "series_spec.json"
    if series_spec_path.exists():
        add_auxiliary_file(
            out,
            series_spec_path,
            aux_dir,
            None if not multi_sequence else f"{slug}_series_spec.json",
        )

    previews_dir = out_path / "previews"
    if previews_dir.is_dir():
        for png in sorted(previews_dir.glob("*.png")):
            add_auxiliary_file(
                out,
                png,
                aux_dir,
                None if not multi_sequence else f"{slug}_{png.name}",
            )

    # camrie-tools-v1 writes previews directly into the sequence output
    # directory. Keep accepting the established previews/ layout as well.
    for png in sorted(out_path.glob("recon_*.png")):
        add_auxiliary_file(
            out,
            png,
            aux_dir,
            None if not multi_sequence else f"{slug}_{png.name}",
        )


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

        task_info = info_json["task"]
        opts = task_info["options"]

        pipelineid = info_json.get("pipeline") or task_info.get("pipeline")
        token = info_json.get("token")
        user_id = info_json.get("user_id")
        logger.write(f"pipeline={pipelineid}  user={user_id}")

        # ── 2. Download body model / tissue maps once ───────────────────────
        tissue = resolve_tissue_maps(opts, s3)
        rho_path = tissue["rho"]
        t1_path = tissue["t1"]
        t2_path = tissue.get("t2")
        auto_isocenter_mm = compute_auto_isocenter(rho_path)
        logger.write(f"Auto isocenter from body model: {auto_isocenter_mm}")

        # ── 3. Normalize one or more sequence requests ──────────────────────
        sequence_jobs = normalize_sequence_jobs(opts)
        multi_sequence = len(sequence_jobs) > 1
        logger.write(f"Sequence count: {len(sequence_jobs)}")

        # ── 4. Output directory ─────────────────────────────────────────────
        out_base = create_random_temp_dir()
        out_root = out_base / "OUT"
        aux_dir = out_base / "AUX"
        os.makedirs(out_root, exist_ok=True)
        aux_dir.mkdir(parents=True, exist_ok=True)
        logger.write(f"Output root: {out_root}")

        # ── 5. Package outputs with cmrOutput ───────────────────────────────
        #
        # ID convention (matches old CAMRIE lambda):
        #   1   – Reconstruction (final 3-D NIfTI volume)
        #  10   – K-space real
        #  11   – K-space imaginary
        #  12   – K-space magnitude
        # For multiple sequences, IDs are offset by sequence_index * 100.
        #
        out = ca.cmrOutput(app="CAMRIE")
        out.setPipeline(pipelineid)
        out.setToken(token)
        out.out["user_id"] = user_id

        sequence_results = []
        for job in sequence_jobs:
            seq_path = download_from_s3(job["descriptor"], s3)
            geo = job["geometry"]
            sim = job["simulation"]
            isocenter_mm = geo.get("isocenter_mm") or auto_isocenter_mm
            out_dir = out_root / job["slug"]
            out_dir.mkdir(parents=True, exist_ok=True)

            logger.write(
                f"Starting sequence {job['index']}/{len(sequence_jobs)}: {job['name']} "
                f"({geo['num_slices']} slices, B0={sim['b0']}T, "
                f"normal={geo['slice_normal']}, spin_factor={sim['spin_factor']})"
            )

            pipeline.run_pipeline(
                rho_path=rho_path,
                t1_path=t1_path,
                t2_path=t2_path,
                sequence_file=seq_path,
                output_dir=str(out_dir),
                isocenter_mm=isocenter_mm,
                slice_normal=geo["slice_normal"],
                num_slices=geo["num_slices"],
                slice_thickness_mm=geo["slice_thickness_mm"],
                slice_gap_mm=geo["slice_gap_mm"],
                fov_mm=geo["fov_mm"],
                seq_fov_mm=geo["seq_fov_mm"],
                matrix=geo["matrix"],
                spin_factor=sim["spin_factor"],
                b0=sim["b0"],
                use_gpu=sim["use_gpu"],
                n_threads=sim["n_threads"],
                parallel_slices=sim["parallel_slices"],
                apply_hamming=sim["apply_hamming"],
                spins_per_voxel=sim["spins_per_voxel"],
                spin_method=pipeline.normalize_spin_methods(sim["spin_method"]),
                slice_padding=sim["slice_padding"],
                t2star_factor=sim["t2star_factor"],
                debug=False,
            )
            logger.write(f"Sequence completed: {job['name']}")
            add_sequence_outputs(out, out_dir, job, multi_sequence, aux_dir)

            sequence_results.append({
                "index": job["index"],
                "name": job["name"],
                "sequence_file": seq_path,
                "output_dir": str(out_dir),
                "geometry": geo,
                "simulation": sim,
                "isocenter_mm": isocenter_mm,
                "status": "succeeded",
            })

        manifest = {
            "schema": "camrie.multi_sequence.v1",
            "pipeline": pipelineid,
            "makeitkoma_sha": os.getenv("MAKEITKOMA_SHA", "unknown"),
            "sequence_count": len(sequence_results),
            "bodymodel_source": tissue["source"],
            "bodymodel": {
                "root_dir": tissue.get("bodymodel", {}).get("root_dir")
                if tissue.get("bodymodel") else None,
                "info_path": tissue.get("bodymodel", {}).get("info_path")
                if tissue.get("bodymodel") else None,
            },
            "tissue_maps": {
                "rho": rho_path,
                "t1": t1_path,
                "t2": t2_path,
            },
            "sequences": sequence_results,
            "output_request": info_json.get("output"),
        }
        manifest_path = aux_dir / "camrie_manifest.json"
        write_json_file(manifest_path, manifest)
        out.addAuxiliaryFile(str(manifest_path))

        # Keep the old top-level info keys for single-sequence consumers, while
        # adding the richer manifest for frontend multi-sequence jobs.
        first = sequence_results[0]
        first_sim = first["simulation"]
        first_geo = first["geometry"]
        out.out["info"] = sanitize_for_json({
            "sequence_count": len(sequence_results),
            "sequences": sequence_results,
            "num_slices": first_geo["num_slices"],
            "b0": first_sim["b0"],
            "spin_factor": first_sim["spin_factor"],
            "spins_per_voxel": first_sim["spins_per_voxel"],
            "parallel_slices": first_sim["parallel_slices"],
            "n_threads": first_sim["n_threads"],
            "slice_padding": first_sim["slice_padding"],
            "use_gpu": first_sim["use_gpu"],
            "apply_hamming": first_sim["apply_hamming"],
            "slice_normal": first_geo["slice_normal"],
            "isocenter_mm": first["isocenter_mm"],
            "slice_thickness_mm": first_geo["slice_thickness_mm"],
            "slice_gap_mm": first_geo["slice_gap_mm"],
        })

        out.setLog(logger)
        out.setOptions(opts)
        out.setTask(task_info)

        # ── 6. Export & upload ──────────────────────────────────────────────
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
