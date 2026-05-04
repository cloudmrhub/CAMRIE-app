#!/usr/bin/env bash
# create-ecr-repo.sh — Create the CAMRIE ECR repository (run once).
set -euo pipefail

REGION="us-east-1"
PROFILE="nyu"
REPO_NAME="camrie-fargate"

aws ecr create-repository \
  --repository-name "$REPO_NAME" \
  --region "$REGION" \
  --profile "$PROFILE" \
  --image-scanning-configuration scanOnPush=true \
  --image-tag-mutability MUTABLE \
  --output table \
  2>/dev/null || echo "Repository '${REPO_NAME}' already exists."

echo "ECR repo: $(aws ecr describe-repositories \
  --repository-names "$REPO_NAME" \
  --region "$REGION" \
  --profile "$PROFILE" \
  --query 'repositories[0].repositoryUri' \
  --output text)"
