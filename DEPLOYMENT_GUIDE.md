# CAMRIE-app Deployment Guide

Deploy the KomaMRI Fargate backend to AWS using GitHub Actions.

## Prerequisites

- AWS account with profile `nyu` configured locally
- GitHub admin access to `cloudmrhub/CAMRIE-app` repo
- cloudmrhub admin credentials (email + password)
- GitHub CLI (`gh`) installed and authenticated

## Step 1: One-Time Setup (Local Machine)

### 1a. Set up GitHub secrets

This stores AWS credentials and API config needed by the CI/CD workflows.

```bash
cd /data/PROJECTS/CAMRIE-app

# Populate all 9 required secrets
CLOUDMR_ADMIN_EMAIL="your-admin-email@example.com" \
CLOUDMR_ADMIN_PASSWORD="your-admin-password" \
./scripts/setup-github-secrets.sh cloudmrhub/CAMRIE-app
```

This will:
- Auto-pull VPC config from the `mroptimum-app-test` CloudFormation stack
- Prompt for cloudmrhub admin credentials
- Login to get a fresh admin token
- Set 9 secrets on the GitHub repo (AWS role, subnets, security group, API host, admin token)

### 1b. Create ECR repository (optional, if not auto-created)

```bash
./scripts/create-ecr-repo.sh
```

This creates the ECR repo `camrie-fargate` where Docker images will be stored.

### 1c. Add GitHub PAT for private makeitKOMA access

The build workflow needs to clone the private `cloudmrhub/makeitKOMA` repo.

1. **Create a fine-grained Personal Access Token on GitHub:**
   - Go to https://github.com/settings/personal-access-tokens/new
   - **Name**: `CAMRIE makeitKOMA read`
   - **Resource owner**: Select `cloudmrhub` (the org)
   - **Repository access**: Select only `cloudmrhub/makeitKOMA`
   - **Permissions**: Set **Contents** to `Read-only`
   - **Generate** and copy the token

2. **Store it as a GitHub secret:**
   ```bash
   gh secret set GH_PAT --repo cloudmrhub/CAMRIE-app
   ```
   Paste the token when prompted.

## Step 2: Deploy (Automated via GitHub Actions)

### 2a. Commit and push to main

Once setup secrets are in place, any push to `main` triggers the workflows:

```bash
git add -A
git commit -m "Your message"
git push origin main
```

### 2b. GitHub Actions runs automatically

Two workflows run in sequence:

**1. `build-images.yml`** — Triggered when `calculation/src/**` changes
   - Checks out the private `cloudmrhub/makeitKOMA` repo
   - Copies pipeline files (`MRI_pipeline_dev.py`, `simulate_batch_final.jl`)
   - Builds Docker image with Python 3.11 + Julia 1.10 + KomaMRI
   - Pushes image to ECR as `camrie-fargate:latest`

**2. `deploy-and-register.yml`** — Triggered after build completes
   - **Deploys** the CloudFormation stack (`camrie-app-prod`)
     - Creates ECS cluster, Fargate task definition, Step Functions state machine
     - Lambda `RunJobLambda` that launches Fargate tasks
     - Outputs: `CalculationStateMachineArn`, `RunJobLambdaArn`
   - **Registers** the computing unit with cloudmrhub
     - Calls `/api/computing-unit/register` on the cloudmrhub brain API
     - Mode: `mode_1` (Fargate batch processing)
     - Status shown in cloudmrhub UI

### 2c. Monitor the workflow

In GitHub:
- Go to `cloudmrhub/CAMRIE-app` → **Actions**
- Click the workflow run to see logs
- Check step outputs for deployed resource ARNs

## Step 3: Verify Deployment

### Check CloudFormation stack

```bash
aws --profile nyu cloudformation describe-stacks \
  --stack-name camrie-app-prod \
  --query "Stacks[0].StackStatus" \
  --output text
```

Should return: `CREATE_COMPLETE` or `UPDATE_COMPLETE`

### Get outputs

```bash
aws --profile nyu cloudformation describe-stacks \
  --stack-name camrie-app-prod \
  --query "Stacks[0].Outputs[*].[OutputKey,OutputValue]" \
  --output table
```

Outputs:
- `CalculationStateMachineArn` — Step Functions state machine to trigger jobs
- `RunJobLambdaArn` — Lambda function (entry point)

### Check ECR image

```bash
aws --profile nyu ecr describe-images \
  --repository-name camrie-fargate \
  --query "imageDetails[?imageTags[0]=='latest']"
```

### Verify in cloudmrhub UI

1. Log in to https://brain.aws.cloudmrhub.com
2. Go to **Computing Units**
3. Look for "CAMRIE" mode_1 unit
4. Status should be **Available** (green)

## Step 4: Test a Job

### Submit a test MRI simulation

Using the cloudmrhub API:

```bash
curl -X POST "https://f41j488v7j.execute-api.us-east-1.amazonaws.com/Prod/api/pipeline/request" \
  -H "Authorization: Bearer ${CLOUDMR_ADMIN_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{
    "appId": "koma-mri",
    "mode": "mode_1",
    "params": {
      "rho": "0.5",
      "T1": "1.5",
      "T2": "0.1",
      "sequence": "FLASH"
    }
  }'
```

This will:
1. Queue the job in cloudmrhub
2. Trigger the CAMRIE Step Functions state machine
3. Launch a Fargate task with the parameters
4. Run `MRI_pipeline.py` → `simulate_batch_final.jl`
5. Upload results to S3 buckets

### Monitor job

In AWS Console:
- Go to **Step Functions** → `CARIECalculationStateMachine`
- Click the execution to see the Fargate task run

In cloudmrhub UI:
- Go to **Jobs** → filter by "CAMRIE"
- Watch status change: `pending` → `running` → `completed`

## Troubleshooting

### Build fails: "repository not found" for makeitKOMA

- **Cause**: `GH_PAT` secret not set or expired
- **Fix**: Recreate a fresh PAT at https://github.com/settings/personal-access-tokens/new and update:
  ```bash
  gh secret set GH_PAT --repo cloudmrhub/CAMRIE-app
  ```

### Deploy fails: CloudFormation rollback

- Check the **Events** tab in CloudFormation console
- Common causes:
  - Subnet IDs or Security Group ID invalid
  - IAM role missing permissions
  - ECR image not found

### Fargate task fails

- Go to AWS ECS → cluster `camrie-app-prod-cluster`
- Click the task → check **Logs** and **Stopped reason**
- Common issues:
  - Missing environment variable (check `app.py` expects: `FILE_EVENT`, `AWS_REGION`)
  - S3 bucket permissions (role needs s3:GetObject, s3:PutObject)
  - Julia package precompilation failed

### Login to cloudmrhub fails in workflow

- Check `deploy-and-register.yml` logs for the exact error
- Verify `CLOUDMR_ADMIN_EMAIL` and `CLOUDMR_ADMIN_PASSWORD` are correct
- If changed recently, update secrets:
  ```bash
  gh secret set CLOUDMR_ADMIN_PASSWORD --repo cloudmrhub/CAMRIE-app
  ```

## Maintenance

### Update pipeline code

Push changes to `calculation/src/**`:

```bash
# Edit MRI_pipeline.py or adjust Docker setup
git add calculation/src/
git commit -m "Update pipeline"
git push origin main
```

The workflows will automatically rebuild the image and redeploy.

### Update pipeline from makeitKOMA

The build workflow fetches from `cloudmrhub/makeitKOMA@camrie-tools-v1`. To update:

1. Make changes in makeitKOMA repo
2. Tag the commit as `camrie-tools-v1` (or update the ref in `build-images.yml`)
3. Push to GitHub
4. Commit an empty change to CAMRIE-app to trigger the workflow:
   ```bash
   git commit --allow-empty -m "Rebuild with latest makeitKOMA"
   git push origin main
   ```

### Rollback to previous stack

```bash
aws --profile nyu cloudformation cancel-update-stack \
  --stack-name camrie-app-prod
```

Or delete and redeploy:

```bash
aws --profile nyu cloudformation delete-stack \
  --stack-name camrie-app-prod

# Wait for deletion to complete, then push to main to redeploy
git commit --allow-empty -m "Redeploy stack"
git push origin main
```

---

**Questions?** Check the logs in:
- GitHub Actions: `cloudmrhub/CAMRIE-app` → Actions tab
- CloudFormation: AWS Console → CloudFormation → Stacks → `camrie-app-prod` → Events
- ECS: AWS Console → ECS → Clusters → `camrie-app-prod-cluster` → Tasks
