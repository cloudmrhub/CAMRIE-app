#!/usr/bin/env bash
# =============================================================================
# CAMRIE  –  local integration test
#
# Runs the full pipeline locally using the 'koma' conda environment.
# No Docker, no Fargate.  Results land in  calculation/local_out/
#
# Usage
# -----
#   ./run_local_test.sh                         # generate packaged phantom + run
#   ./run_local_test.sh --seq /path/to/epi.seq  # provide your own sequence
#   ./run_local_test.sh --event eventGPU.json   # choose a separate event file
#   ./run_local_test.sh --skip-phantom          # reuse existing phantom/
#   ./run_local_test.sh --voxel-mm 1.0          # finer phantom resolution
#
# Tissue parameters (override defaults via env vars or flags below)
#   --inner-t1 800  --inner-t2 60  --inner-pd 1.0   (inner cylinder)
#   --outer-t1 1200 --outer-t2 80  --outer-pd 0.8   (outer ring)
#   --inner-r 3     --outer-r 6    --height 5        (geometry in cm)
#
# Simulation parameters (patch event.json before running)
#   --event PATH                 Event JSON to patch and run (default: event.json)
#   --b0 1.5                    B0 field strength in Tesla (default: from event.json)
#   --spins-per-voxel-gre 64    Extra spins per voxel for GRE (default: from event.json)
#   --use-model-axial-normal    Force axial slice normal [0,0,1]
#   --parallel-slices 2         Worker threads for slice extraction/recon
#   --jobs 2                    Julia/CPU threads for simulation
#   --spin-factor 4             Spin lattice density factor
#   --slice-padding 0.5         Slice padding multiplier
#   --num-slices 250            Number of slices to simulate
#   --gpu                       Enable GPU simulation (requires CUDA + KomaMRI GPU backend)
#
# What it does
# ------------
#   1. Check 'koma' conda env is present
#   2. (Re)generate the packaged concentric-cylinder NIfTI phantom
#   3. Patch event.json with the chosen sequence path
#   4. Run  src/local_test.py  via conda run -n koma
#   5. Print location of results ZIP
# =============================================================================

set -euo pipefail

# ── locate this script so paths are repo-relative ────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
SRC="${SCRIPT_DIR}/src"
PHANTOM_DIR="${SCRIPT_DIR}/phantom"
OUT_DIR="${SCRIPT_DIR}/local_out"
EVENT_FILE="${SCRIPT_DIR}/event.json"

# ── defaults ──────────────────────────────────────────────────────────────────
SEQ_FILE=""
SKIP_PHANTOM=false
VOXEL_MM=2.0
INNER_R=3.0;  OUTER_R=6.0;  HEIGHT=5.0
INNER_PD=1.0; INNER_T1=800;  INNER_T2=60
OUTER_PD=0.8; OUTER_T1=1200; OUTER_T2=80

# Simulation overrides (empty = keep whatever is already in event.json)
SIM_B0=""
SIM_SPINS_PER_VOXEL=""
SIM_USE_AXIAL_NORMAL=false
SIM_PARALLEL_SLICES=""
SIM_JOBS=""
SIM_SPIN_FACTOR=""
SIM_SLICE_PADDING=""
SIM_NUM_SLICES=""
SIM_GPU=false

# ── parse arguments ───────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
    case "$1" in
        --seq)                   SEQ_FILE="$2";              shift 2 ;;
        --event)                 EVENT_FILE="$2";            shift 2 ;;
        --skip-phantom)          SKIP_PHANTOM=true;          shift   ;;
        --voxel-mm)              VOXEL_MM="$2";              shift 2 ;;
        --inner-r)               INNER_R="$2";               shift 2 ;;
        --outer-r)               OUTER_R="$2";               shift 2 ;;
        --height)                HEIGHT="$2";                shift 2 ;;
        --inner-pd)              INNER_PD="$2";              shift 2 ;;
        --inner-t1)              INNER_T1="$2";              shift 2 ;;
        --inner-t2)              INNER_T2="$2";              shift 2 ;;
        --outer-pd)              OUTER_PD="$2";              shift 2 ;;
        --outer-t1)              OUTER_T1="$2";              shift 2 ;;
        --outer-t2)              OUTER_T2="$2";              shift 2 ;;
        # Simulation overrides
        --b0)                    SIM_B0="$2";                shift 2 ;;
        --spins-per-voxel-gre|--spins-per-voxel) SIM_SPINS_PER_VOXEL="$2"; shift 2 ;;
        --use-model-axial-normal) SIM_USE_AXIAL_NORMAL=true; shift   ;;
        --parallel-slices)       SIM_PARALLEL_SLICES="$2";  shift 2 ;;
        --jobs)                  SIM_JOBS="$2";              shift 2 ;;
        --spin-factor)           SIM_SPIN_FACTOR="$2";       shift 2 ;;
        --slice-padding)         SIM_SLICE_PADDING="$2";     shift 2 ;;
        --num-slices)            SIM_NUM_SLICES="$2";        shift 2 ;;
        --gpu)                   SIM_GPU=true;               shift   ;;
        -h|--help)
            sed -n '2,/^# ====/p' "$0" | grep '^#' | sed 's/^# \?//'
            exit 0 ;;
        *) echo "Unknown option: $1" >&2; exit 1 ;;
    esac
done

if [[ "${EVENT_FILE}" != /* ]]; then
    EVENT_FILE="${SCRIPT_DIR}/${EVENT_FILE}"
fi
[[ -f "${EVENT_FILE}" ]] || { echo "Event file not found: ${EVENT_FILE}" >&2; exit 1; }

# ── helpers ───────────────────────────────────────────────────────────────────
ok()   { echo "  ✓  $*"; }
fail() { echo "  ✗  $*" >&2; exit 1; }
info() { echo "  →  $*"; }
section() { echo; echo "── $* ──────────────────────────────────────────"; }


# ── 1. Environment checks ─────────────────────────────────────────────────────
section "Checking environment"

conda run -n koma python --version >/dev/null 2>&1 \
    || fail "'koma' conda environment not found or cannot run Python. Create it first."
ok "conda env 'koma' found"

conda run -n koma python - <<'PYEOF' >/dev/null 2>&1 || fail "Python package 'camrie-tools' is not installed from cloudmrhub/camrie-tools@v1 in conda env 'koma'.
       Install/update the local dev package with:
         pip install --upgrade --force-reinstall "camrie-tools @ git+https://github.com/cloudmrhub/camrie-tools.git@v1"
       Then install Julia deps with:
         camrie-install-julia --cpu --update    # CPU local dev
         camrie-install-julia --update          # GPU local dev"
import importlib.metadata as md, json
from pathlib import Path
from camrie_tools import MRI_pipeline
from camrie_tools._reconstruction_smoke import create_concentric_cylinder_phantom
dist = md.distribution("camrie-tools")
direct = Path(dist._path) / "direct_url.json"
data = json.loads(direct.read_text()) if direct.exists() else {}
url = data.get("url", "")
if "cloudmrhub/camrie-tools" not in url.replace(".git", ""):
    raise SystemExit(f"camrie-tools is not installed from cloudmrhub/camrie-tools: {url or dist.locate_file('')}")
PYEOF
ok "camrie-tools git package importable"

CAMRIE_JULIA_PROJECT="${CAMRIE_JULIA_PROJECT:-$(conda run -n koma python -c 'from camrie_tools._julia import DEFAULT_PROJECT_DIR; print(DEFAULT_PROJECT_DIR)')}"
export CAMRIE_JULIA_PROJECT
export JULIA_PROJECT="${CAMRIE_JULIA_PROJECT}"
ok "CAMRIE Julia project: ${CAMRIE_JULIA_PROJECT}"

# ── 2. Phantom ────────────────────────────────────────────────────────────────
section "Phantom"

if [[ "${SKIP_PHANTOM}" == true && -f "${PHANTOM_DIR}/rho.nii" ]]; then
    ok "Reusing existing phantom in ${PHANTOM_DIR}"
else
    info "Generating phantom (voxel=${VOXEL_MM} mm, inner=${INNER_R} cm, outer=${OUTER_R} cm, h=${HEIGHT} cm) ..."
    conda run -n koma python - "${PHANTOM_DIR}" "${VOXEL_MM}" \
        "${INNER_R}" "${OUTER_R}" "${HEIGHT}" \
        "${INNER_PD}" "${INNER_T1}" "${INNER_T2}" \
        "${OUTER_PD}" "${OUTER_T1}" "${OUTER_T2}" <<'PYEOF'
from pathlib import Path
import sys

import numpy as np
import SimpleITK as sitk
from camrie_tools._reconstruction_smoke import create_concentric_cylinder_phantom

out_dir = Path(sys.argv[1])
voxel_mm = float(sys.argv[2])
inner_radius_mm = float(sys.argv[3]) * 10.0
outer_radius_mm = float(sys.argv[4]) * 10.0
height_mm = float(sys.argv[5]) * 10.0
inner_pd = float(sys.argv[6])
inner_t1_ms = float(sys.argv[7])
inner_t2_ms = float(sys.argv[8])
outer_pd = float(sys.argv[9])
outer_t1_ms = float(sys.argv[10])
outer_t2_ms = float(sys.argv[11])

# camrie-tools defines the local smoke phantom as a Koma spin phantom.
# CAMRIE local events still consume rho/t1/t2 NIfTI maps, so rasterize
# that packaged definition into a centered 3D cylinder for local testing.
fov_mm = max(300.0, 2.5 * outer_radius_mm)
grid_size = max(3, int(round((0.9 * fov_mm) / voxel_mm)) + 1)
if grid_size % 2 == 0:
    grid_size += 1

phantom = create_concentric_cylinder_phantom(
    inner_radius_mm=inner_radius_mm,
    outer_radius_mm=outer_radius_mm,
    fov_mm=fov_mm,
    grid_size=grid_size,
    inner_pd=inner_pd,
    inner_t1_ms=inner_t1_ms,
    inner_t2_ms=inner_t2_ms,
    outer_pd=outer_pd,
    outer_t1_ms=outer_t1_ms,
    outer_t2_ms=outer_t2_ms,
)

axis = np.linspace(-0.45 * fov_mm, 0.45 * fov_mm, grid_size, dtype=np.float32)
spacing_xy = float(axis[1] - axis[0]) if grid_size > 1 else float(voxel_mm)
z_count = max(1, int(round(height_mm / spacing_xy)) + 1)
if z_count % 2 == 0:
    z_count += 1
z_spacing = float(height_mm / max(z_count - 1, 1)) if z_count > 1 else float(voxel_mm)

shape = (z_count, grid_size, grid_size)  # SimpleITK array order: z, y, x
rho = np.zeros(shape, dtype=np.float32)
t1 = np.zeros(shape, dtype=np.float32)
t2 = np.zeros(shape, dtype=np.float32)

xs = np.asarray(phantom["x"], dtype=np.float32) * 1000.0
ys = np.asarray(phantom["y"], dtype=np.float32) * 1000.0
rhos = np.asarray(phantom["rho"], dtype=np.float32)
t1_ms = np.asarray(phantom["t1"], dtype=np.float32) * 1000.0
t2_ms = np.asarray(phantom["t2"], dtype=np.float32) * 1000.0
ix = np.clip(np.rint((xs - axis[0]) / spacing_xy).astype(int), 0, grid_size - 1)
iy = np.clip(np.rint((ys - axis[0]) / spacing_xy).astype(int), 0, grid_size - 1)
rho[:, iy, ix] = rhos
t1[:, iy, ix] = t1_ms
t2[:, iy, ix] = t2_ms

out_dir.mkdir(parents=True, exist_ok=True)
origin = (float(axis[0]), float(axis[0]), -0.5 * float(height_mm))
spacing = (spacing_xy, spacing_xy, z_spacing)
for name, array in (("rho", rho), ("t1", t1), ("t2", t2)):
    image = sitk.GetImageFromArray(array)
    image.SetSpacing(spacing)
    image.SetOrigin(origin)
    sitk.WriteImage(image, str(out_dir / f"{name}.nii"))

print(f"  packaged phantom written to {out_dir} shape={shape} spacing={spacing}")
PYEOF
    ok "Phantom written to ${PHANTOM_DIR}"
fi

# ── 3. Sequence ───────────────────────────────────────────────────────────────
section "Sequence"

if [[ -n "${SEQ_FILE}" ]]; then
    [[ -f "${SEQ_FILE}" ]] || fail "Sequence file not found: ${SEQ_FILE}"
    ok "Using sequence: ${SEQ_FILE}"

    # Patch event.json with the real sequence path (in-place, no jq needed)
    python3 - "${EVENT_FILE}" "${SEQ_FILE}" <<'PYEOF'
import json, sys
event_path, seq_path = sys.argv[1], sys.argv[2]
with open(event_path) as f:
    ev = json.load(f)
ev["task"]["options"]["sequence"] = {
    "type": "local",
    "local_path": seq_path,
    "filename": seq_path.rsplit("/", 1)[-1],
}
with open(event_path, "w") as f:
    json.dump(ev, f, indent=4)
print(f"  event.json updated with sequence: {seq_path}")
PYEOF
else
    # Check event.json still has a real seq path (not the placeholder)
    SEQ_IN_EVENT=$(python3 -c "
import json, sys
ev = json.load(open('${EVENT_FILE}'))
print(ev['task']['options'].get('sequence', {}).get('local_path', ''))
")
    if [[ "${SEQ_IN_EVENT}" == "/path/to/sequence.seq" || -z "${SEQ_IN_EVENT}" ]]; then
        fail "No sequence file set.  Either:
       • Pass --seq /path/to/your.seq
       • Edit event.json  →  task.options.sequence.local_path"
    fi
    [[ -f "${SEQ_IN_EVENT}" ]] \
        || fail "Sequence path in event.json does not exist: ${SEQ_IN_EVENT}"
    ok "Using sequence from event.json: ${SEQ_IN_EVENT}"
fi

# ── 3b. Patch simulation / geometry params into event.json ───────────────────
section "Patching simulation parameters"
python3 - "${EVENT_FILE}" \
    "${SIM_B0}" "${SIM_SPINS_PER_VOXEL}" "${SIM_USE_AXIAL_NORMAL}" \
    "${SIM_PARALLEL_SLICES}" "${SIM_JOBS}" "${SIM_SPIN_FACTOR}" \
    "${SIM_SLICE_PADDING}" "${SIM_NUM_SLICES}" "${SIM_GPU}" <<'PYEOF'
import json, sys

event_path = sys.argv[1]
b0, spins_per_voxel, use_axial, parallel_slices, jobs, spin_factor, slice_padding, num_slices, use_gpu = sys.argv[2:11]

with open(event_path) as f:
    ev = json.load(f)

sim = ev["task"]["options"].setdefault("simulation", {})
geo = ev["task"]["options"].setdefault("geometry", {})

if b0:              sim["b0"]              = float(b0)
if spins_per_voxel: sim["spins_per_voxel"] = int(spins_per_voxel)
if parallel_slices: sim["parallel_slices"] = int(parallel_slices)
if jobs:            sim["n_threads"]       = int(jobs)
if spin_factor:     sim["spin_factor"]     = int(spin_factor)
if slice_padding:   sim["slice_padding"]   = float(slice_padding)
if num_slices:      geo["num_slices"]      = int(num_slices)
if use_axial == "true":
    geo["slice_normal"] = [0, 0, 1]
    print("  slice_normal forced to axial [0,0,1]")
if use_gpu == "true":  sim["use_gpu"] = True

with open(event_path, "w") as f:
    json.dump(ev, f, indent=4)

changed = {k: sim[k] for k in ("b0","spins_per_voxel","parallel_slices","n_threads","spin_factor","slice_padding","use_gpu") if k in sim}
changed.update({"num_slices": geo.get("num_slices"), "slice_normal": geo.get("slice_normal")})
print(f"  event.json simulation params: {changed}")
PYEOF
ok "event.json patched"

# ── 4. Run pipeline ───────────────────────────────────────────────────────────
section "Running pipeline"
info "Results will land in: ${OUT_DIR}"
echo

cd "${SCRIPT_DIR}"   # local_path entries in event.json are relative to calculation/
LOCAL_RESULTS_DIR="${OUT_DIR}" \
    conda run --no-capture-output -n koma python -u "${SRC}/local_test.py" "${EVENT_FILE}"

# ── 5. Report ─────────────────────────────────────────────────────────────────
section "Done"
RESULT_ZIP=$(find "${OUT_DIR}" -name "*.zip" -newer "${EVENT_FILE}" 2>/dev/null | sort | tail -1)
if [[ -n "${RESULT_ZIP}" ]]; then
    ok "Result ZIP: ${RESULT_ZIP}"
    info "Contents: open the ZIP with your archive viewer to inspect its files."
else
    echo "  (no new ZIP found — check output above for errors)"
fi
