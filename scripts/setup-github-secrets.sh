#!/usr/bin/env bash
# setup-github-secrets.sh
#
# Sets all GitHub Actions secrets for CAMRIE-app.
# - Reads VPC/network values from the already-deployed mroptimum stack (same infra)
# - Creates the OIDC role if it does not exist
# - Prompts only for admin credentials
#
# Usage:  ./scripts/setup-github-secrets.sh
set -euo pipefail

TARGET_REPO="cloudmrhub/CAMRIE-app"
MRO_STACK="mroptimum-app-test"      # source of subnet/SG/CortexHost values
# NOTE: deploying CAMRIE does NOT touch mroptimum-app-test.
# New stack name is 'camrie-app-prod' — completely separate CF stack,
# separate ECS cluster, separate ECR repo, separate IAM roles.
AWS_PROFILE="${AWS_PROFILE:-nyu}"
AWS_REGION="${AWS_REGION:-us-east-1}"
ROLE_NAME="GitHubActionsRole-CAMRIE-app"
AWS_CMD="aws --profile ${AWS_PROFILE} --region ${AWS_REGION}"

echo "╔═══════════════════════════════════════════════════════════════╗"
echo "║   CAMRIE — GitHub Secrets Setup                               ║"
echo "╚═══════════════════════════════════════════════════════════════╝"
echo "Target repo : ${TARGET_REPO}"
echo "AWS profile : ${AWS_PROFILE}"
echo ""

# ── helper ───────────────────────────────────────────────────────────────────
set_secret() {
  local name="$1" value="$2"
  printf "  %-30s " "${name}"
  printf '%s' "${value}" | gh secret set "${name}" --repo "${TARGET_REPO}"
  echo "✓"
}

# ── 1. OIDC Role ARN ─────────────────────────────────────────────────────────
echo "==> Resolving AWS deploy role…"
ROLE_ARN=$($AWS_CMD iam get-role --role-name "${ROLE_NAME}" \
  --query 'Role.Arn' --output text 2>/dev/null || true)

if [ -z "$ROLE_ARN" ] || [ "$ROLE_ARN" = "None" ]; then
  echo "    Not found — creating OIDC role '${ROLE_NAME}'…"
  "$(dirname "$0")/create-github-oidc-role.sh" cloudmrhub CAMRIE-app
  ROLE_ARN=$($AWS_CMD iam get-role --role-name "${ROLE_NAME}" \
    --query 'Role.Arn' --output text)
fi
echo "    ${ROLE_ARN}"

# ── 2. Subnets + Security Group from the mroptimum stack ─────────────────────
echo ""
echo "==> Reading VPC config from stack '${MRO_STACK}'…"

get_param() {
  $AWS_CMD cloudformation describe-stacks --stack-name "${MRO_STACK}" \
    --query "Stacks[0].Parameters[?ParameterKey=='$1'].ParameterValue" \
    --output text 2>/dev/null || echo ""
}

SUBNET_ID_1=$(get_param "SubnetId1")
SUBNET_ID_2=$(get_param "SubnetId2")
SECURITY_GROUP_ID=$(get_param "SecurityGroupIds")

if [ -z "$SUBNET_ID_1" ] || [ "$SUBNET_ID_1" = "None" ]; then
  echo "    Could not read from stack — listing public subnets…"
  echo ""
  $AWS_CMD ec2 describe-subnets \
    --filters "Name=map-public-ip-on-launch,Values=true" \
    --query "Subnets[*].[SubnetId,AvailabilityZone,CidrBlock]" \
    --output table
  echo ""
  read -r -p "  SUBNET_ID_1: " SUBNET_ID_1
  read -r -p "  SUBNET_ID_2: " SUBNET_ID_2
else
  echo "    SubnetId1:  ${SUBNET_ID_1}"
  echo "    SubnetId2:  ${SUBNET_ID_2}"
fi

if [ -z "$SECURITY_GROUP_ID" ] || [ "$SECURITY_GROUP_ID" = "None" ]; then
  $AWS_CMD ec2 describe-security-groups \
    --query "SecurityGroups[*].[GroupId,GroupName]" --output table
  read -r -p "  SECURITY_GROUP_ID: " SECURITY_GROUP_ID
else
  echo "    SecurityGroup: ${SECURITY_GROUP_ID}"
fi

# ── 3. CloudMR API host ───────────────────────────────────────────────────────
echo ""
echo "==> Resolving CloudMR API endpoint…"
CLOUDMR_API_HOST=$($AWS_CMD cloudformation describe-stacks \
  --stack-name "${MRO_STACK}" \
  --query "Stacks[0].Parameters[?ParameterKey=='CortexHost'].ParameterValue" \
  --output text 2>/dev/null || echo "api.cloudmrhub.com")

[ -z "$CLOUDMR_API_HOST" ] || [ "$CLOUDMR_API_HOST" = "None" ] && \
  CLOUDMR_API_HOST="brain.aws.cloudmrhub.com/Prod"

CLOUDMR_API_URL="https://${CLOUDMR_API_HOST}"
echo "    ${CLOUDMR_API_URL}"

# ── 4. Admin credentials ─────────────────────────────────────────────────────
# gh CLI cannot read secret *values* from another repo (security by design),
# so we cannot copy CLOUDMR_ADMIN_EMAIL/PASSWORD from mroptimum-app.
# Enter the same credentials you used there — they are only used here to get
# a fresh token; only the token is stored as a GitHub secret.
echo ""
echo "==> CloudMR admin credentials (same as mroptimum-app)"
if [ -z "${CLOUDMR_ADMIN_EMAIL:-}" ]; then
  read -r -p "  Admin email:    " CLOUDMR_ADMIN_EMAIL
else
  echo "  Admin email:    ${CLOUDMR_ADMIN_EMAIL}  (from env)"
fi
if [ -z "${CLOUDMR_ADMIN_PASSWORD:-}" ]; then
  read -r -s -p "  Admin password: " CLOUDMR_ADMIN_PASSWORD
  echo ""
else
  echo "  Admin password: ****  (from env)"
fi

echo "    Verifying and obtaining token…"
LOGIN=$(curl -s -X POST "${CLOUDMR_API_URL}/auth/login" \
  -H "Content-Type: application/json" \
  -d "{\"email\":\"${CLOUDMR_ADMIN_EMAIL}\",\"password\":\"${CLOUDMR_ADMIN_PASSWORD}\"}")
TOKEN=$(echo "$LOGIN" | jq -r '.id_token // .idToken // .access_token // empty' 2>/dev/null || true)
if [ -z "$TOKEN" ]; then
  echo "    ⚠  Login failed — double-check email/password"
  echo "    Response: $(echo "$LOGIN" | head -c 300)"
  exit 1
else
  echo "    ✓ Token obtained"
fi

# ── 5. Set secrets ────────────────────────────────────────────────────────────
echo ""
echo "==> Setting secrets on ${TARGET_REPO}…"

set_secret "AWS_DEPLOY_ROLE_ARN"   "${ROLE_ARN}"
set_secret "SUBNET_ID_1"           "${SUBNET_ID_1}"
set_secret "SUBNET_ID_2"           "${SUBNET_ID_2}"
set_secret "SECURITY_GROUP_ID"     "${SECURITY_GROUP_ID}"
set_secret "CLOUDMR_API_HOST"      "${CLOUDMR_API_HOST}"
set_secret "CLOUDMR_API_URL"       "${CLOUDMR_API_URL}"
set_secret "CLOUDMR_ADMIN_EMAIL"   "${CLOUDMR_ADMIN_EMAIL}"
set_secret "CLOUDMR_ADMIN_PASSWORD" "${CLOUDMR_ADMIN_PASSWORD}"
set_secret "CLOUDMR_ADMIN_TOKEN"   "${TOKEN}"

echo ""
echo "╔═══════════════════════════════════════════════════════════════╗"
echo "║   All secrets set ✓                                           ║"
echo "╚═══════════════════════════════════════════════════════════════╝"
echo ""
echo "Push to main to start the build:"
echo "  git add -A && git commit -m 'init camrie backend' && git push origin main"
