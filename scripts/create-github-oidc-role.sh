#!/usr/bin/env bash
# create-github-oidc-role.sh
# Creates the AWS IAM OIDC identity provider + role that lets GitHub Actions
# assume permissions in your AWS account without storing long-lived keys.
#
# Usage:
#   ./create-github-oidc-role.sh <github-org> <github-repo>
#   ./create-github-oidc-role.sh cloudmrhub CAMRIE-app
set -euo pipefail

GITHUB_ORG="${1:-cloudmrhub}"
GITHUB_REPO="${2:-CAMRIE-app}"
AWS_REGION="${AWS_REGION:-us-east-1}"
AWS_PROFILE="${AWS_PROFILE:-nyu}"
ROLE_NAME="GitHubActionsRole-${GITHUB_REPO}"

echo "╔═══════════════════════════════════════════════════════════════╗"
echo "║   Create GitHub Actions OIDC Role — CAMRIE                    ║"
echo "╚═══════════════════════════════════════════════════════════════╝"
echo "GitHub:  ${GITHUB_ORG}/${GITHUB_REPO}"
echo "Role:    ${ROLE_NAME}"
echo "Profile: ${AWS_PROFILE}"
echo ""

AWS_CMD="aws --profile ${AWS_PROFILE} --region ${AWS_REGION}"

# ── 1. OIDC provider ─────────────────────────────────────────────────────────
OIDC_ARN=$(${AWS_CMD} iam list-open-id-connect-providers \
  --query "OpenIDConnectProviderList[?ends_with(Arn,'token.actions.githubusercontent.com')].Arn" \
  --output text)

if [ -z "$OIDC_ARN" ]; then
  echo "==> Creating GitHub OIDC provider…"
  OIDC_ARN=$(${AWS_CMD} iam create-open-id-connect-provider \
    --url https://token.actions.githubusercontent.com \
    --client-id-list sts.amazonaws.com \
    --thumbprint-list 6938fd4d98bab03faadb97b34396831e3780aea1 \
    --query "OpenIDConnectProviderArn" --output text)
  echo "    Created: ${OIDC_ARN}"
else
  echo "==> OIDC provider already exists: ${OIDC_ARN}"
fi

AWS_ACCOUNT_ID=$(${AWS_CMD} sts get-caller-identity --query Account --output text)

# ── 2. Trust policy ───────────────────────────────────────────────────────────
TRUST_POLICY=$(cat <<EOF
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": {
      "Federated": "arn:aws:iam::${AWS_ACCOUNT_ID}:oidc-provider/token.actions.githubusercontent.com"
    },
    "Action": "sts:AssumeRoleWithWebIdentity",
    "Condition": {
      "StringLike": {
        "token.actions.githubusercontent.com:sub": "repo:${GITHUB_ORG}/${GITHUB_REPO}:*"
      },
      "StringEquals": {
        "token.actions.githubusercontent.com:aud": "sts.amazonaws.com"
      }
    }
  }]
}
EOF
)

# ── 3. Create or update role ──────────────────────────────────────────────────
if ${AWS_CMD} iam get-role --role-name "${ROLE_NAME}" >/dev/null 2>&1; then
  echo "==> Updating trust policy on existing role ${ROLE_NAME}…"
  ${AWS_CMD} iam update-assume-role-policy \
    --role-name "${ROLE_NAME}" \
    --policy-document "${TRUST_POLICY}"
else
  echo "==> Creating role ${ROLE_NAME}…"
  ${AWS_CMD} iam create-role \
    --role-name "${ROLE_NAME}" \
    --assume-role-policy-document "${TRUST_POLICY}" \
    --description "GitHub Actions OIDC role for ${GITHUB_ORG}/${GITHUB_REPO}" \
    --output table
fi

# ── 4. Attach permissions ─────────────────────────────────────────────────────
echo "==> Attaching managed policies…"
for POLICY in \
  "arn:aws:iam::aws:policy/AmazonECS_FullAccess" \
  "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryFullAccess" \
  "arn:aws:iam::aws:policy/AWSCloudFormationFullAccess" \
  "arn:aws:iam::aws:policy/IAMFullAccess" \
  "arn:aws:iam::aws:policy/AWSStepFunctionsFullAccess" \
  "arn:aws:iam::aws:policy/AmazonS3FullAccess" \
  "arn:aws:iam::aws:policy/CloudWatchLogsFullAccess"; do
  ${AWS_CMD} iam attach-role-policy \
    --role-name "${ROLE_NAME}" \
    --policy-arn "${POLICY}" 2>/dev/null || true
  echo "    Attached: ${POLICY##*/}"
done

# SAM needs PassRole to hand the execution role to ECS
PASSROLE_POLICY=$(cat <<EOF
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Action": ["iam:PassRole", "lambda:*"],
    "Resource": "*"
  }]
}
EOF
)
${AWS_CMD} iam put-role-policy \
  --role-name "${ROLE_NAME}" \
  --policy-name "PassRoleAndLambda" \
  --policy-document "${PASSROLE_POLICY}"

ROLE_ARN=$(${AWS_CMD} iam get-role --role-name "${ROLE_NAME}" \
  --query 'Role.Arn' --output text)

echo ""
echo "╔═══════════════════════════════════════════════════════════════╗"
echo "║   Done                                                        ║"
echo "╚═══════════════════════════════════════════════════════════════╝"
echo "Role ARN (add as GitHub secret AWS_DEPLOY_ROLE_ARN):"
echo "  ${ROLE_ARN}"
