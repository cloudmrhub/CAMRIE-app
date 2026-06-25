#!/usr/bin/env bash
# build-and-push.sh — Build the CAMRIE Fargate Docker image and push to ECR.
#
# Usage:
#   ./scripts/build-and-push.sh [--region us-east-1] [--tag latest]
#
# Prerequisites:
#   • AWS CLI configured with profile "nyu"
#   • Docker running
#   • ECR repo already created (see create-ecr-repo.sh)
set -euo pipefail

REGION="us-east-1"
PROFILE="nyu"
TAG="latest"
REPO_NAME="camrie-fargate"
CAMRIE_TOOLS_REF="${CAMRIE_TOOLS_REF:-v1}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --region)  REGION="$2";  shift 2 ;;
    --tag)     TAG="$2";     shift 2 ;;
    --profile) PROFILE="$2"; shift 2 ;;
    *) echo "Unknown option: $1" >&2; exit 1 ;;
  esac
done

ACCOUNT_ID=$(aws sts get-caller-identity --profile "$PROFILE" --query Account --output text)
ECR_URI="${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com/${REPO_NAME}:${TAG}"

echo "==> Logging in to ECR …"
aws ecr get-login-password --region "$REGION" --profile "$PROFILE" \
  | docker login --username AWS --password-stdin "${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com"

echo "==> Building Fargate image …"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DOCKER_CONTEXT="${SCRIPT_DIR}/../calculation/src"


docker build \
  --build-arg CAMRIE_TOOLS_REF="${CAMRIE_TOOLS_REF}" \
  -f "${DOCKER_CONTEXT}/DockerfileFargate" \
  -t "${ECR_URI}" \
  "${DOCKER_CONTEXT}"

echo "==> Pushing ${ECR_URI} …"
docker push "${ECR_URI}"

echo ""
echo "Image pushed: ${ECR_URI}"
echo "Pass this as FargateImageUri when deploying."
