#!/usr/bin/env bash
# =============================================================================
# CAMRIE  –  local integration test
#
# Runs the full pipeline locally using the 'koma' conda environment.
# No Docker, no Fargate.  Results land in  calculation/local_out/
#
# Usage
# -----
#   ./run_local_test.sh                         # generate phantom + run
#   ./run_local_test.sh --seq /path/to/epi.seq  # provide your own sequence
#   ./run_local_test.sh --skip-phantom          # reuse existing phantom/
#   ./run_local_test.sh --voxel-mm 1.0          # finer phantom resolution
#
# Tissue parameters (override defaults via env vars or flags below)
#   --inner-t1 800  --inner-t2 60  --inner-pd 1.0   (inner cylinder)
#   --outer-t1 1200 --outer-t2 80  --outer-pd 0.8   (outer ring)
#   --inner-r 3     --outer-r 6    --height 5        (geometry in cm)
#
# What it does
# ------------
#   1. Check 'koma' conda env is present
#   2. Auto-copy MRI_pipeline.py + simulate_batch_final.jl from
#      /data/PROJECTS/makeitKOMA/dev/  (if not already in src/)
#   3. (Re)generate the concentric-cylinder NIfTI phantom
#   4. Patch event.json with the chosen sequence path
#   5. Run  src/local_test.py  via conda run -n koma
#   6. Print location of results ZIP
# =============================================================================

set -euo pipefail

# ── locate this script so paths are repo-relative ────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
SRC="${SCRIPT_DIR}/src"
PHANTOM_DIR="${SCRIPT_DIR}/phantom"
OUT_DIR="${SCRIPT_DIR}/local_out"
EVENT_FILE="${SCRIPT_DIR}/event.json"
PIPELINE_PY="${SRC}/MRI_pipeline.py"
PIPELINE_JL="${SRC}/simulate_batch_final.jl"

# ── defaults ──────────────────────────────────────────────────────────────────
SEQ_FILE=""
SKIP_PHANTOM=false
VOXEL_MM=2.0
INNER_R=3.0;  OUTER_R=6.0;  HEIGHT=5.0
INNER_PD=1.0; INNER_T1=800;  INNER_T2=60
OUTER_PD=0.8; OUTER_T1=1200; OUTER_T2=80

# ── parse arguments ───────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
    case "$1" in
        --seq)           SEQ_FILE="$2";     shift 2 ;;
        --skip-phantom)  SKIP_PHANTOM=true; shift   ;;
        --voxel-mm)      VOXEL_MM="$2";     shift 2 ;;
        --inner-r)       INNER_R="$2";      shift 2 ;;
        --outer-r)       OUTER_R="$2";      shift 2 ;;
        --height)        HEIGHT="$2";       shift 2 ;;
        --inner-pd)      INNER_PD="$2";     shift 2 ;;
        --inner-t1)      INNER_T1="$2";     shift 2 ;;
        --inner-t2)      INNER_T2="$2";     shift 2 ;;
        --outer-pd)      OUTER_PD="$2";     shift 2 ;;
        --outer-t1)      OUTER_T1="$2";     shift 2 ;;
        --outer-t2)      OUTER_T2="$2";     shift 2 ;;
        -h|--help)
            sed -n '2,/^# ====/p' "$0" | grep '^#' | sed 's/^# \?//'
            exit 0 ;;
        *) echo "Unknown option: $1" >&2; exit 1 ;;
    esac
done

# ── helpers ───────────────────────────────────────────────────────────────────
ok()   { echo "  ✓  $*"; }
fail() { echo "  ✗  $*" >&2; exit 1; }
info() { echo "  →  $*"; }
section() { echo; echo "── $* ──────────────────────────────────────────"; }

# Known local checkout of makeitKOMA (used for auto-copy below)
MAKEITKOMA_DEV="/data/PROJECTS/makeitKOMA/dev"

# ── 1. Environment checks ─────────────────────────────────────────────────────
section "Checking environment"

conda info --envs 2>/dev/null | grep -q "^koma " \
    || fail "'koma' conda environment not found.  Create it first."
ok "conda env 'koma' found"

# Auto-copy pipeline files from the local makeitKOMA checkout if missing
if [[ ! -f "${PIPELINE_PY}" ]]; then
    if [[ -f "${MAKEITKOMA_DEV}/MRI_pipeline_dev.py" ]]; then
        cp "${MAKEITKOMA_DEV}/MRI_pipeline_dev.py" "${PIPELINE_PY}"
        ok "Auto-copied MRI_pipeline.py from ${MAKEITKOMA_DEV}"
    else
        fail "MRI_pipeline.py not found at ${PIPELINE_PY}
       Copy it manually:
         cp /path/to/makeitKOMA/dev/MRI_pipeline_dev.py ${PIPELINE_PY}"
    fi
else
    ok "MRI_pipeline.py present"
fi

if [[ ! -f "${PIPELINE_JL}" ]]; then
    if [[ -f "${MAKEITKOMA_DEV}/simulate_batch_final.jl" ]]; then
        cp "${MAKEITKOMA_DEV}/simulate_batch_final.jl" "${PIPELINE_JL}"
        ok "Auto-copied simulate_batch_final.jl from ${MAKEITKOMA_DEV}"
    else
        fail "simulate_batch_final.jl not found at ${PIPELINE_JL}
       Copy it manually:
         cp /path/to/makeitKOMA/dev/simulate_batch_final.jl ${PIPELINE_JL}"
    fi
else
    ok "simulate_batch_final.jl present"
fi

# ── 2. Phantom ────────────────────────────────────────────────────────────────
section "Phantom"

if [[ "${SKIP_PHANTOM}" == true && -f "${PHANTOM_DIR}/rho.nii" ]]; then
    ok "Reusing existing phantom in ${PHANTOM_DIR}"
else
    info "Generating phantom (voxel=${VOXEL_MM} mm, inner=${INNER_R} cm, outer=${OUTER_R} cm, h=${HEIGHT} cm) ..."
    conda run -n koma python "${SCRIPT_DIR}/make_phantom.py" \
        --out-dir    "${PHANTOM_DIR}" \
        --voxel-mm   "${VOXEL_MM}"   \
        --inner-r    "${INNER_R}"    \
        --outer-r    "${OUTER_R}"    \
        --height     "${HEIGHT}"     \
        --inner-pd   "${INNER_PD}"   \
        --inner-t1   "${INNER_T1}"   \
        --inner-t2   "${INNER_T2}"   \
        --outer-pd   "${OUTER_PD}"   \
        --outer-t1   "${OUTER_T1}"   \
        --outer-t2   "${OUTER_T2}"
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

# ── 4. Run pipeline ───────────────────────────────────────────────────────────
section "Running pipeline"
info "Results will land in: ${OUT_DIR}"
echo

cd "${SCRIPT_DIR}"   # local_path entries in event.json are relative to calculation/
LOCAL_RESULTS_DIR="${OUT_DIR}" \
    conda run -n koma python "${SRC}/local_test.py" "${EVENT_FILE}"

# ── 5. Report ─────────────────────────────────────────────────────────────────
section "Done"
RESULT_ZIP=$(find "${OUT_DIR}" -name "*.zip" -newer "${EVENT_FILE}" 2>/dev/null | sort | tail -1)
if [[ -n "${RESULT_ZIP}" ]]; then
    ok "Result ZIP: ${RESULT_ZIP}"
    info "Contents:"
    python3 -c "
import zipfile, sys
with zipfile.ZipFile('${RESULT_ZIP}') as z:
    for n in z.namelist():
        info = z.getinfo(n)
        print(f'    {info.file_size:>10,}  {n}')
"
else
    echo "  (no new ZIP found — check output above for errors)"
fi
