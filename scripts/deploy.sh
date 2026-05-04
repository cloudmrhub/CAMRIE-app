#!/usr/bin/env bash
# deploy.sh — Build image (optional) then deploy the CAMRIE SAM stack.
#
# Usage:
#   ./scripts/deploy.sh --subnet1 subnet-xxx --subnet2 subnet-yyy \
#                       --sg sg-zzz [--stage Prod] [--skip-build]
set -euo pipefail

PROFILE="nyu"
REGION="us-east-1"
STAGE="Prod"
TAG="latest"
REPO_NAME="camrie-fargate"
SKIP_BUILD=0

# Required
SUBNET1=""
SUBNET2=""
SG=""

usage() {
  cat <<EOF
Usage: $0 --subnet1 <id> --subnet2 <id> --sg <id> [options]

Required:
  --subnet1 ID    First public subnet
  --subnet2 ID    Second public subnet
  --sg ID         Security group

Optional:
  --stage NAME    Prod|Dev|Test (default: Prod)
  --region NAME   AWS region (default: us-east-1)
  --tag TAG       Docker image tag (default: latest)
  --skip-build    Skip docker build/push (reuse existing ECR image)
  --profile NAME  AWS CLI profile (default: nyu)
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --subnet1)    SUBNET1="$2";     shift 2 ;;
    --subnet2)    SUBNET2="$2";     shift 2 ;;
    --sg)         SG="$2";          shift 2 ;;
    --stage)      STAGE="$2";       shift 2 ;;
    --region)     REGION="$2";      shift 2 ;;
    --tag)        TAG="$2";         shift 2 ;;
    --skip-build) SKIP_BUILD=1;     shift   ;;
    --profile)    PROFILE="$2";     shift 2 ;;
    -h|--help)    usage; exit 0 ;;
    *) echo "Unknown option: $1"; usage; exit 1 ;;
  esac
done

if [[ -z "$SUBNET1" || -z "$SUBNET2" || -z "$SG" ]]; then
  echo "ERROR: --subnet1, --subnet2, and --sg are required." >&2
  usage; exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ACCOUNT_ID=$(aws sts get-caller-identity --profile "$PROFILE" --query Account --output text)
ECR_URI="${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com/${REPO_NAME}:${TAG}"

# ── 1. (Optional) Build & push image ──────────────────────────────────────────
if [[ "$SKIP_BUILD" -eq 0 ]]; then
  echo "==> Building and pushing Docker image …"
  "${SCRIPT_DIR}/build-and-push.sh" --region "$REGION" --tag "$TAG" --profile "$PROFILE"
else
  echo "==> Skipping Docker build (--skip-build)"
fi

# ── 2. SAM build ──────────────────────────────────────────────────────────────
echo "==> SAM build …"
cd "${SCRIPT_DIR}/.."
sam build --profile "$PROFILE"

# ── 3. SAM deploy ─────────────────────────────────────────────────────────────
STAGE_LOWER=$(echo "$STAGE" | tr '[:upper:]' '[:lower:]')
echo "==> SAM deploy (config-env: ${STAGE_LOWER}) …"
sam deploy \
  --config-env "${STAGE_LOWER}" \
  --profile "$PROFILE" \
  --region "$REGION" \
  --parameter-overrides \
      "FargateImageUri=${ECR_URI}" \
      "SubnetId1=${SUBNET1}" \
      "SubnetId2=${SUBNET2}" \
      "SecurityGroupIds=${SG}" \
      "StageName=${STAGE}"

echo ""
echo "Deployment complete."
