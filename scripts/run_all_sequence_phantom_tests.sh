#!/usr/bin/env bash
#
# Submit every CAMRIE .seq and .mtrk sequence against:
#   1. the concentric cylindrical phantom
#   2. the asymmetric (non-spherical) phantom
#
# Run from WSL:
#   export CLOUDMR_TOKEN='...'
#   bash scripts/run_all_sequence_phantom_tests.sh
#
# Optional:
#   USE_GPU=1 bash scripts/run_all_sequence_phantom_tests.sh
#   TIMEOUT=7200 bash scripts/run_all_sequence_phantom_tests.sh
#   SEQUENCE_GLOB='*.mtrk' bash scripts/run_all_sequence_phantom_tests.sh

set -uo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SEQUENCE_DIR="${REPO_DIR}/data/sequences"
CYLINDRICAL_PHANTOM="${REPO_DIR}/calculation/phantom"
NONSPHERICAL_PHANTOM="${REPO_DIR}/calculation/local_out/cloud_phantoms/asymmetric"

CONDA_ENV="${CONDA_ENV:-koma}"
TIMEOUT="${TIMEOUT:-3600}"
USE_GPU="${USE_GPU:-0}"
SEQUENCE_GLOB="${SEQUENCE_GLOB:-*}"
RUN_STAMP="$(date +%Y%m%d-%H%M%S)"
RUN_DIR="${REPO_DIR}/calculation/local_out/all-sequence-tests/${RUN_STAMP}"
SUMMARY_FILE="${RUN_DIR}/summary.tsv"

if [[ -z "${CLOUDMR_TOKEN:-}" ]]; then
    echo "ERROR: CLOUDMR_TOKEN is not set." >&2
    echo "Run: export CLOUDMR_TOKEN='YOUR_TOKEN'" >&2
    exit 2
fi

for phantom_dir in "${CYLINDRICAL_PHANTOM}" "${NONSPHERICAL_PHANTOM}"; do
    for tissue_map in rho.nii t1.nii t2.nii; do
        if [[ ! -f "${phantom_dir}/${tissue_map}" ]]; then
            echo "ERROR: Missing ${phantom_dir}/${tissue_map}" >&2
            exit 2
        fi
    done
done

mapfile -t SEQUENCES < <(
    find "${SEQUENCE_DIR}" -maxdepth 1 -type f \
        \( -name '*.seq' -o -name '*.mtrk' \) \
        -name "${SEQUENCE_GLOB}" \
        -print | sort
)

if (( ${#SEQUENCES[@]} == 0 )); then
    echo "ERROR: No .seq or .mtrk files matched in ${SEQUENCE_DIR}" >&2
    exit 2
fi

mkdir -p "${RUN_DIR}"
printf 'phantom\tsequence\tstatus\texit_code\tlog\tresults\n' > "${SUMMARY_FILE}"

PHANTOM_NAMES=(cylindrical nonspherical)
PHANTOM_DIRS=("${CYLINDRICAL_PHANTOM}" "${NONSPHERICAL_PHANTOM}")
GPU_ARGS=()
COMPUTE_NAME="CPU"
if [[ "${USE_GPU}" == "1" ]]; then
    GPU_ARGS+=(--use-gpu --monitor-task)
    COMPUTE_NAME="GPU"
fi

total=$(( ${#SEQUENCES[@]} * ${#PHANTOM_NAMES[@]} ))
passed=0
failed=0
test_number=0

echo "CAMRIE exhaustive sequence/phantom cloud test"
echo "Compute:    ${COMPUTE_NAME}"
echo "Sequences:  ${#SEQUENCES[@]}"
echo "Phantoms:   ${#PHANTOM_NAMES[@]}"
echo "Total jobs: ${total}"
echo "Run output: ${RUN_DIR}"
echo

for phantom_index in "${!PHANTOM_NAMES[@]}"; do
    phantom_name="${PHANTOM_NAMES[$phantom_index]}"
    phantom_dir="${PHANTOM_DIRS[$phantom_index]}"

    for sequence_path in "${SEQUENCES[@]}"; do
        test_number=$((test_number + 1))
        sequence_file="$(basename "${sequence_path}")"
        sequence_name="${sequence_file%.*}"
        sequence_ext="${sequence_file##*.}"
        test_name="${phantom_name}__${sequence_name}__${sequence_ext}"
        test_dir="${RUN_DIR}/${test_name}"
        log_file="${RUN_DIR}/${test_name}.log"
        mkdir -p "${test_dir}"

        echo "[$test_number/$total] ${phantom_name} + ${sequence_file}"

        command=(
            conda run -n "${CONDA_ENV}" python "${REPO_DIR}/scripts/run_cloud_test.py"
            --token "${CLOUDMR_TOKEN}"
            --seq-file "${sequence_path}"
            --phantom-dir "${phantom_dir}"
            --alias "CAMRIE ${phantom_name} - ${sequence_file}"
            --b0 1.5
            --num-slices 1
            --spin-factor 1
            --spins-per-voxel 0
            --parallel-slices 1
            --n-threads 1
            --timeout "${TIMEOUT}"
            --output-dir "${test_dir}"
        )
        command+=("${GPU_ARGS[@]}")

        set +e
        "${command[@]}" 2>&1 | tee "${log_file}"
        exit_code=${PIPESTATUS[0]}
        set -e

        if (( exit_code == 0 )); then
            status="PASS"
            passed=$((passed + 1))
        else
            status="FAIL"
            failed=$((failed + 1))
        fi

        printf '%s\t%s\t%s\t%s\t%s\t%s\n' \
            "${phantom_name}" "${sequence_file}" "${status}" "${exit_code}" \
            "${log_file}" "${test_dir}" >> "${SUMMARY_FILE}"
        echo "Result: ${status}"
        echo
    done
done

echo "============================================================"
echo "CAMRIE test summary"
echo "Passed:  ${passed}"
echo "Failed:  ${failed}"
echo "Total:   ${total}"
echo "Details: ${SUMMARY_FILE}"
echo "============================================================"
column -t -s $'\t' "${SUMMARY_FILE}" 2>/dev/null || cat "${SUMMARY_FILE}"

if (( failed > 0 )); then
    exit 1
fi
