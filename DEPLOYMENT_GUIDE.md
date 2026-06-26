# CAMRIE-app Deployment Guide

Deploy the KomaMRI AWS Batch backend to AWS using GitHub Actions.

---

## Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [Prerequisites](#prerequisites)
3. [Step 1 – One-Time Setup](#step-1-one-time-setup)
4. [Step 2 – Deploy via GitHub Actions](#step-2-deploy-via-github-actions)
5. [Step 3 – Verify Deployment](#step-3-verify-deployment)
6. [Step 4 – Run a Test](#step-4-run-a-test)
7. [scripts/run_cloud_test.py Reference](#scriptsrun_cloud_testpy-reference)
8. [GPU Path Deep Dive](#gpu-path-deep-dive)
9. [Maintenance & Operational Runbook](#maintenance--operational-runbook)
10. [Troubleshooting](#troubleshooting)

---

## Architecture Overview

CAMRIE has **two compute paths** submitted through AWS Batch. The router Lambda
uses the job size and `use_gpu` hint to choose the cheapest viable queue:

| | CPU path | GPU path |
|---|---|---|
| Batch compute | Fargate Spot | EC2 Spot GPU |
| Docker image tag | `camrie-fargate:latest` | `camrie-fargate:gpu-latest` |
| vCPU / RAM | 4 vCPU / 16 GB | 4 vCPU / 15 GB + 1 GPU |
| Scale-to-zero | Native Fargate | Batch EC2 compute env with `MinvCpus: 0` |
| Cold-start | image pull + app start | Spot instance launch + image pull |
| CloudWatch log group | `/ecs/camrie-Prod` | `/ecs/camrie-gpu-Prod` |

Both paths share the same Step Functions state machine, S3 buckets, and CloudMR Brain API routing via `RunJobLambda`.

```
CloudMR Brain API
    │
    ▼
Lambda RunJobLambda
    ├─ small/CPU jobs → AWS Batch CPU queue → Fargate Spot
    └─ large/GPU jobs → AWS Batch GPU queue → EC2 Spot GPU
```

### Nested stack structure

```
camrie-app-prod  (root template.yaml)
└── CalculationApp  (calculation/template.yaml)
    ├── CpuComputeEnvironment      (Batch Fargate Spot)
    ├── CpuJobQueue / CpuJobDefinition
    ├── GpuComputeEnvironment      [condition: GpuImageUri != ""]
    ├── GpuJobQueue / GpuJobDefinition
    ├── GpuBatchInstanceRole / Profile
    ├── RunJobLambda               (routes to Batch CPU or GPU)
    └── CalculationStateMachine
```

GPU Batch resources are gated by the `GpuEnabled` condition. If `GpuImageUri`
is empty, CPU Batch still deploys and GPU routing falls back to CPU.

### `camrie-tools` package and Julia runtime

CAMRIE-app depends on the installable package:

```text
camrie-tools @ git+https://github.com/cloudmrhub/camrie-tools.git@v1
```

The package owns the MRI pipeline Python code, bundled `simulate_batch.jl`, the
Julia dependency installer, and the package smoke phantom. CAMRIE-app owns the
AWS Batch/Lambda/Docker wrapper.

Both Docker images install `camrie-tools@v1` and then run the package installer
inside a dedicated Julia project:

```text
CAMRIE Julia project: /opt/camrie-julia
Julia depot:          /opt/julia-depot
```

CPU image:

```bash
camrie-install-julia --cpu --update --project-dir /opt/camrie-julia --install-dir /opt/julia-depot
```

GPU image:

```bash
camrie-install-julia --update --project-dir /opt/camrie-julia --install-dir /opt/julia-depot
```

Both images bake a Julia sysimage at Docker build time:

```text
CPU sysimage: /opt/julia-depot/komamri-sysimage.so
GPU sysimage: /opt/julia-depot/komamri-gpu-sysimage.so
```

The container keeps the production command as `julia`, but the image wraps it so
every runtime Julia process starts with the dedicated Julia project and the
sysimage:

```text
/opt/julia/bin/julia --project=/opt/camrie-julia --sysimage=<sysimage> --sysimage-native-code=no
```

`--sysimage-native-code=no` is intentional. GitHub Actions may build the image on
a different CPU generation than AWS Fargate or the GPU EC2 instance. Disabling
native-code reuse lets Julia load the sysimage contents without trying to execute
runner-specific cached machine code. The sysimage still includes the direct Julia
packages installed by `camrie-tools` (`KomaInterface` and, for GPU, `CUDA`) and
lets PackageCompiler include transitive dependencies. For diagnosis, set
`CAMRIE_DISABLE_JULIA_SYSIMAGE=1` in a direct Batch container override to run with
`/opt/camrie-julia` but without the sysimage.

---

## Prerequisites

- AWS account with profile `nyu` configured locally (`aws configure --profile nyu`)
- GitHub admin access to `cloudmrhub/CAMRIE-app`
- CloudMRHub admin credentials (email + password)
- GitHub CLI (`gh`) installed and authenticated
- `sam` CLI ≥ 1.80
- `jq`, `python3`, `boto3`

---

## Step 1 – One-Time Setup

### 1a. Create ECR repository

```bash
./scripts/create-ecr-repo.sh
```

Creates `camrie-fargate` in ECR where both CPU and GPU images are stored.

### 1b. Set GitHub secrets

```bash
CLOUDMR_ADMIN_EMAIL="you@example.com" \
CLOUDMR_ADMIN_PASSWORD="yourpassword" \
./scripts/setup-github-secrets.sh cloudmrhub/CAMRIE-app
```

Secrets set:

| Secret | Description |
|---|---|
| `AWS_DEPLOY_ROLE_ARN` | OIDC role for GitHub Actions to assume |
| `SUBNET_ID_1` | Private subnet for ECS tasks |
| `SUBNET_ID_2` | Second private subnet |
| `SECURITY_GROUP_ID` | SG attached to tasks |
| `CLOUDMR_API_HOST` | API Gateway hostname |
| `CLOUDMR_API_URL` | Full Brain API base URL |
| `CLOUDMR_ADMIN_EMAIL` | Admin login email |
| `CLOUDMR_ADMIN_PASSWORD` | Admin login password |
| `CLOUDMR_ADMIN_TOKEN` | Pre-issued JWT (optional fallback) |

### 1c. Package source

The Docker builds install `cloudmrhub/camrie-tools@v1` directly over HTTPS.
No extra GitHub PAT is required for the package as long as the repository stays
public/readable to GitHub Actions.

### 1d. Create OIDC trust for GitHub Actions

```bash
./scripts/create-github-oidc-role.sh
```

Grants the `AWS_DEPLOY_ROLE_ARN` role the permissions needed to deploy the SAM stack.

---

## Step 2 – Deploy via GitHub Actions

### CI/CD workflow overview

Two workflows run automatically on push to `main`:

#### `build-images.yml` — Triggered when `calculation/src/**` or the workflow changes

1. Checks out CAMRIE-app.
2. Builds the **CPU image** from `DockerfileFargate` and installs `camrie-tools@${CAMRIE_TOOLS_REF}`.
3. Builds the **GPU image** from `DockerfileGpu` and installs `camrie-tools@${CAMRIE_TOOLS_REF}`.
4. Pushes `camrie-fargate:latest`, `camrie-fargate:<sha>`, `camrie-fargate:gpu-latest`, and `camrie-fargate:gpu-<sha>`.

The default package ref is:

```yaml
CAMRIE_TOOLS_REF: v1
```

Key environment variables baked into the images:

| Variable | CPU image | GPU image |
|---|---|---|
| `CAMRIE_TOOLS_REF` | `v1` by default | `v1` by default |
| `CAMRIE_JULIA_PROJECT` | `/opt/camrie-julia` | `/opt/camrie-julia` |
| `JULIA_PROJECT` | `/opt/camrie-julia` | `/opt/camrie-julia` |
| `JULIA_DEPOT_PATH` | `/opt/julia-depot` | `/opt/julia-depot` |
| `JULIA_CPU_TARGET` | `generic` | `generic` |
| `JULIA_PKG_DISABLE_PKGIMAGES` | `true` | *(not set)* |
| Base image | `python:3.11-slim` + Julia 1.12.6 | `nvidia/cuda:12.6.3-runtime-ubuntu22.04` + Julia 1.12.6 |

#### `deploy-and-register.yml` — Triggered after build completes (or on template changes)

A **concurrency group** (`deploy-${{ github.ref }}`, `cancel-in-progress: false`) ensures deploys are queued rather than run in parallel (prevents CloudFormation `UPDATE_IN_PROGRESS` collisions).

Steps:
1. Resolve image digests from ECR (`latest` and `gpu-latest`)
2. `sam build`
3. `sam deploy` with all parameter overrides
4. Check if a `mode_1` computing unit is already registered
5. Register with CloudMR Brain if not already registered

### Manual deploy (local)

```bash
ACCOUNT=469266894233
REGION=us-east-1

FARGATE_DIGEST=$(AWS_PAGER="" aws ecr describe-images \
  --repository-name camrie-fargate \
  --image-ids imageTag=latest \
  --region $REGION --profile nyu \
  --query 'imageDetails[0].imageDigest' --output text)

GPU_DIGEST=$(AWS_PAGER="" aws ecr describe-images \
  --repository-name camrie-fargate \
  --image-ids imageTag=gpu-latest \
  --region $REGION --profile nyu \
  --query 'imageDetails[0].imageDigest' --output text)

AWS_PAGER="" sam deploy \
  --stack-name camrie-app-prod \
  --region $REGION --profile nyu \
  --resolve-s3 \
  --parameter-overrides \
    FargateImageUri=${ACCOUNT}.dkr.ecr.${REGION}.amazonaws.com/camrie-fargate@${FARGATE_DIGEST} \
    GpuImageUri=${ACCOUNT}.dkr.ecr.${REGION}.amazonaws.com/camrie-fargate@${GPU_DIGEST} \
    CortexHost=f41j488v7j.execute-api.us-east-1.amazonaws.com \
    CloudMRBrainStackName=cloudmrhub-brain \
    ECSClusterName=camrie-app-prod-cluster \
    SubnetId1=subnet-0277d2885bd91891d \
    SubnetId2=subnet-0a1f978c87190b19c \
    SecurityGroupIds=sg-0924028ba877265b7 \
    StageName=Prod \
  --capabilities CAPABILITY_IAM CAPABILITY_AUTO_EXPAND CAPABILITY_NAMED_IAM \
  --no-confirm-changeset --no-fail-on-empty-changeset
```

---

## Step 3 – Verify Deployment

### Stack status

```bash
AWS_PAGER="" aws cloudformation describe-stacks \
  --stack-name camrie-app-prod \
  --region us-east-1 --profile nyu \
  --query "Stacks[0].[StackStatus,LastUpdatedTime]" \
  --output table
```

Expected: `UPDATE_COMPLETE`

### Stack outputs

```bash
AWS_PAGER="" aws cloudformation describe-stacks \
  --stack-name camrie-app-prod \
  --region us-east-1 --profile nyu \
  --query "Stacks[0].Outputs[*].[OutputKey,OutputValue]" \
  --output table
```

Key outputs:
- `CalculationStateMachineArn`
- `CpuJobQueueArn`
- `GpuJobQueueArn` *(only present when GPU enabled)*

### Current image digests in ECR

```bash
AWS_PAGER="" aws ecr describe-images \
  --repository-name camrie-fargate \
  --region us-east-1 --profile nyu \
  --query "imageDetails[?contains(imageTags,'latest') || contains(imageTags,'gpu-latest')].[imageTags,imageDigest,imagePushedAt]" \
  --output table
```

### Batch compute status

```bash
AWS_PAGER="" aws batch describe-compute-environments \
  --region us-east-1 --profile nyu \
  --query "computeEnvironments[?contains(computeEnvironmentName,'camrie')].[computeEnvironmentName,state,status,computeResources.type,computeResources.minvCpus,computeResources.desiredvCpus,computeResources.maxvCpus]" \
  --output table
```

GPU EC2 Spot compute should keep `minvCpus` at `0` so idle capacity scales down.

### Batch queue status

```bash
AWS_PAGER="" aws batch describe-job-queues \
  --region us-east-1 --profile nyu \
  --query "jobQueues[?contains(jobQueueName,'camrie')].[jobQueueName,state,status,priority]" \
  --output table
```

### EC2 instances currently running

```bash
AWS_PAGER="" aws ec2 describe-instances \
  --region us-east-1 --profile nyu \
  --filters "Name=tag:aws:ecs:clusterName,Values=camrie-app-prod-cluster" \
  --query "Reservations[].Instances[].[InstanceId,State.Name,LaunchTime,InstanceType]" \
  --output table
```

---

## Step 4 – Run a Test

Use `scripts/run_cloud_test.py` to submit one small CPU job and one small GPU
job through the CloudMR Brain API. The script uploads `calculation/phantom`
(`rho.nii`, `t1.nii`, `t2.nii`) and the selected sequence, queues a job, and
polls until completion.

Set a token first.

PowerShell:

```powershell
$env:CLOUDMR_TOKEN = "PASTE_TOKEN_HERE"
```

Bash/WSL:

```bash
export CLOUDMR_TOKEN='PASTE_TOKEN_HERE'
```

### CPU test (Fargate)

```powershell
conda run -n koma python scripts/run_cloud_test.py `
  --token "$env:CLOUDMR_TOKEN" `
  --seq-file data/sequences/PD-Weighted_Spin_Echo.seq `
  --phantom-dir calculation/phantom `
  --alias "CAMRIE CPU package test" `
  --num-slices 1 `
  --spin-factor 1 `
  --spins-per-voxel 0 `
  --parallel-slices 1 `
  --n-threads 1 `
  --timeout 1800
```

Expected cold-start: image pull + app startup, usually much faster than the GPU
first run.

### GPU test (EC2 Spot GPU)

```powershell
conda run -n koma python scripts/run_cloud_test.py `
  --token "$env:CLOUDMR_TOKEN" `
  --seq-file data/sequences/PD-Weighted_Spin_Echo.seq `
  --phantom-dir calculation/phantom `
  --alias "CAMRIE GPU package test" `
  --num-slices 1 `
  --spin-factor 1 `
  --spins-per-voxel 1 `
  --parallel-slices 1 `
  --n-threads 1 `
  --use-gpu `
  --timeout 3600
```

Expected timeline on a fresh GPU instance:

- EC2 Spot instance launch and image pull
- Julia package/sysimage startup
- CUDA runtime/PTX/JIT warmup on first execution
- Koma simulation and result upload

A second GPU task on a warm instance should start faster.

---

## scripts/run_cloud_test.py Reference

End-to-end test script: uploads phantom + sequence → queues job → monitors ECS task → tails CloudWatch logs → polls until completion.

### Usage

```
python scripts/run_cloud_test.py [OPTIONS]
```

### Authentication

```bash
# Option A: pre-issued JWT (recommended)
python scripts/run_cloud_test.py --token $TOKEN ...

# Option B: email + password (script logs in and obtains token)
python scripts/run_cloud_test.py --api-user you@example.com --api-pass secret ...

# Obtain a token interactively and store in $TOKEN
export TOKEN=$(python3 -c "
import requests, getpass
email = input('Email: ')
password = getpass.getpass('Password: ')
r = requests.post('https://brain.aws.cloudmrhub.com/Prod/api/auth/login',
    json={'email': email, 'password': password})
r.raise_for_status()
print(r.json()['id_token'])
")
```

### Arguments

#### Required

| Argument | Description |
|---|---|
| `--seq-file PATH` | Path to a Pulseq `.seq` file |
| `--token JWT` | JWT id_token (or use `--api-user`/`--api-pass`) |

#### Simulation

| Argument | Default | Description |
|---|---|---|
| `--phantom-dir DIR` | `calculation/phantom` | Directory with `rho.nii`, `t1.nii`, `t2.nii` |
| `--b0 FLOAT` | `1.5` | Main field strength (Tesla) |
| `--num-slices INT` | `4` | Number of slices to simulate |
| `--spin-factor INT` | `4` | Spins per slice (higher = more accurate, slower) |
| `--spins-per-voxel INT` | `1` | Spins per voxel |
| `--parallel-slices INT` | `1` | Slices computed in parallel |
| `--slice-padding FLOAT` | `0.5` | Slice padding factor |
| `--n-threads INT` | `4` | CPU threads for the simulation |
| `--use-gpu` | off | Route job to GPU container (`g4dn.xlarge`) |
| `--alias STR` | `"Cloud Test – Cylindrical Phantom"` | Pipeline display name |

#### Control

| Argument | Default | Description |
|---|---|---|
| `--timeout INT` | `600` | Max seconds to poll for completion |
| `--no-poll` | off | Submit job and exit immediately (returns pipeline ID) |
| `--logs-only TASK_ID` | – | Skip upload/submit; tail logs for an existing ECS task ID |

#### AWS / Logging

| Argument | Default | Description |
|---|---|---|
| `--aws-profile STR` | `nyu` | AWS CLI profile for CloudWatch + ECS API calls |
| `--aws-region STR` | `us-east-1` | AWS region |
| `--log-group STR` | auto | `/ecs/camrie-Prod` (CPU) or `/ecs/camrie-gpu-Prod` (GPU) |
| `--tail-logs` | off | Tail CloudWatch logs while polling |
| `--monitor-task` | off | Show ECS lifecycle stages + cost estimate (auto-enabled with `--use-gpu`) |
| `--cluster STR` | `camrie-app-prod-cluster` | ECS cluster name |
| `--api-base URL` | `https://brain.aws.cloudmrhub.com/Prod/api` | CloudMR Brain API base |

### What the script does (step by step)

1. **Login** — validates or obtains a JWT token
2. **Upload** — multipart-uploads `rho.nii`, `t1.nii`, `t2.nii`, and the `.seq` file via the Brain upload API; returns S3 keys
3. **Queue job** — POSTs to `/pipeline/queue_job`; the Brain API triggers the Step Functions state machine which launches the ECS task
4. **ECS task monitor** (background thread, GPU auto-enabled) — polls `describe_tasks` every 15 s, prints stage transitions (`PROVISIONING → PENDING → RUNNING → STOPPED`) with elapsed timestamps and a cost estimate on completion
5. **wait_for_log_stream** (GPU only) — polls CloudWatch every 10 s until the task's log stream has ≥ 1 event; prints a heartbeat every 30 s with current ECS running-task count so you know the silent CUDA PTX JIT is still in progress
6. **Log tail** — starts `aws logs tail --follow --log-stream-name-prefix <task>` in a background subprocess, filtering logs to the specific task
7. **Poll** — calls `/pipeline/{id}` every 15 s until status is `completed` or `failed` (or timeout)

### Typical invocations

```bash
# Fast CPU test (no GPU, small job)
python scripts/run_cloud_test.py \
  --token $TOKEN \
  --seq-file data/sequences/T1-Weighted_Spin_Echo.seq \
  --phantom-dir calculation/phantom \
  --b0 1.5 --num-slices 2 --spin-factor 4 \
  --tail-logs

# Full GPU test with extended timeout
python scripts/run_cloud_test.py \
  --token $TOKEN \
  --seq-file /path/to/tse_ETL1_T1w.seq \
  --phantom-dir calculation/phantom \
  --b0 1.5 --num-slices 4 --spin-factor 16 \
  --tail-logs --use-gpu --timeout 2400

# Just tail logs for a task you already submitted
python scripts/run_cloud_test.py \
  --token $TOKEN \
  --logs-only 68fd28af141442d7a034fd9f72c90d81 \
  --use-gpu
```

### Expected output

```
═══ Step 1: Login ═══
  ✓ Using provided JWT token

═══ Step 2: Upload Files ═══
  → Initiating upload: rho.nii (456652 bytes)
  ✓ Uploaded → s3://.../CAMRIE/.../rho.nii
  ...

═══ Step 3: Queue Job ═══
  ✓ Job queued!
  → Pipeline:      25a4886f-3509-4ecd-9539-b36ee7ab9c4c
  → Execution ARN: arn:aws:states:us-east-1:...:execution:...:...
  → Log group:     /ecs/camrie-gpu-Prod
  → Compute:       GPU (g4dn.xlarge / EC2-backed ECS)

═══ ECS Task Monitor ═══
  → Task: 68fd28af141442d7a034fd9f72c90d81
  → Instance: g4dn.xlarge
  Waiting for log stream: camrie-gpu/camrie-worker/68fd28af...
  (GPU: Julia loads precompiled cache, then CUDA PTX JIT — silent for up to 15 min)
  [12:45:55] +    0s  PROVISIONING  (waiting for EC2 instance to register)
  [12:46:10] +   15s  PENDING       (instance ready, pulling image)
  [30s] Still initializing... (ECS running tasks: 1) — waiting for first log line
  [12:47:11] +   76s  RUNNING       (container started — cold start was 76s)
  ...
  [12:56:20] +  625s  STOPPED       exit=0

  ── Cost estimate (g4dn.xlarge) ──
  Cold start:       76s
  Simulation:      549s
  Total:           625s
  Rate:          $0.5260/hr
  Est. cost:     $0.0814

═══ Step 5: Polling (timeout=2400s) ═══
  ✓ Simulation completed!
```

---

## GPU Path Deep Dive

### EC2 instance lifecycle

| Stage | Duration | What is happening |
|---|---|---|
| PROVISIONING | 30–90 s | ASG launches `g4dn.xlarge`; instance registers with ECS |
| PENDING | 10–30 s | Docker pulls GPU image from ECR |
| RUNNING (silent) | 5–10 min (cold) / ~30 s (warm) | Julia loads precompiled packages from image depot (~30s); CUDA PTX kernels JIT-compiled for T4 (~5–10 min, first task on fresh instance only); nothing written to logs yet |
| RUNNING (active) | simulation duration | Julia/KomaMRI computes; logs stream continuously |
| STOPPED | — | Results uploaded to S3; container exits 0 |

### Controlling EC2 warm-standby

Keep an instance warm for repeated testing (avoids the 60 s provisioning wait):

```bash
# Scale up — keeps 1 instance always warm (~$0.526/hr)
AWS_PAGER="" aws autoscaling set-desired-capacity \
  --auto-scaling-group-name <GpuASGName> \
  --desired-capacity 1 \
  --region us-east-1 --profile nyu

# Scale to zero after testing (no idle cost)
AWS_PAGER="" aws autoscaling update-auto-scaling-group \
  --auto-scaling-group-name <GpuASGName> \
  --min-size 0 --desired-capacity 0 \
  --region us-east-1 --profile nyu
```

Get the ASG name:
```bash
AWS_PAGER="" aws cloudformation list-stack-resources \
  --stack-name camrie-app-prod-CalculationApp-GU98PSAXQN3Q \
  --region us-east-1 --profile nyu \
  --query "StackResourceSummaries[?ResourceType=='AWS::AutoScaling::AutoScalingGroup'].PhysicalResourceId" \
  --output text
```

### GPU CloudWatch logs

```bash
# Live tail for a specific task
aws logs tail /ecs/camrie-gpu-Prod \
  --follow \
  --log-stream-name-prefix camrie-gpu/camrie-worker/<TASK_ID> \
  --profile nyu --region us-east-1

# List recent GPU log streams
AWS_PAGER="" aws logs describe-log-streams \
  --log-group-name /ecs/camrie-gpu-Prod \
  --region us-east-1 --profile nyu \
  --order-by LastEventTime --descending \
  --query 'logStreams[*].[logStreamName,lastEventTimestamp]' \
  --output text
```

---

## Maintenance & Operational Runbook

### Update pipeline code (`camrie-tools`)

Pipeline code, the bundled Julia batch script, the Julia installer, and the
package smoke phantom live in `cloudmrhub/camrie-tools`. CAMRIE images install
that package from the ref configured in `.github/workflows/build-images.yml`:

```yaml
CAMRIE_TOOLS_REF: v1
```

After changing `camrie-tools`, push the change to `v1` or update
`CAMRIE_TOOLS_REF` to a tag/SHA, then rebuild CAMRIE images:

```bash
git commit --allow-empty -m "Rebuild: pull latest camrie-tools package"
git push origin main
```

### Force rebuild images

Go to GitHub → Actions → **Build & Push Docker Image** → **Run workflow** → check **Force rebuild**.

### Rollback to a previous image

```bash
# List recent GPU images by push date
AWS_PAGER="" aws ecr describe-images \
  --repository-name camrie-fargate \
  --region us-east-1 --profile nyu \
  --query "imageDetails[?contains(imageTags[0],'gpu-')].[imageTags,imageDigest,imagePushedAt]" \
  --output table
```

Then redeploy using the desired digest as `GpuImageUri`.

### Delete and redeploy the stack

```bash
AWS_PAGER="" aws cloudformation delete-stack \
  --stack-name camrie-app-prod \
  --region us-east-1 --profile nyu

# Wait for DELETE_COMPLETE, then push to trigger redeploy
git commit --allow-empty -m "Redeploy stack"
git push origin main
```

---

## Troubleshooting

### CI: Deploy fails — CloudFormation UPDATE_IN_PROGRESS collision

Two workflow runs triggered simultaneously (one from `workflow_run` after a build, one from a direct template-changing push) can collide. The `concurrency` group in `deploy-and-register.yml` queues them. If you still see it, re-run the failed workflow from the GitHub Actions UI — the stack will be in `UPDATE_COMPLETE` by then.

### CI: Build fails while installing `camrie-tools`

The Docker build installs the package with:

```bash
pip install "camrie-tools @ git+https://github.com/cloudmrhub/camrie-tools.git@${CAMRIE_TOOLS_REF}"
```

Check that `CAMRIE_TOOLS_REF` exists on `cloudmrhub/camrie-tools` and that the
repository is readable by GitHub Actions.

### CloudFormation: GpuTaskDefinition CREATE_FAILED

| Error message | Fix |
|---|---|
| `"NVIDIA_VISIBLE_DEVICES" is a reserved variable` | Remove `NVIDIA_VISIBLE_DEVICES` from `ContainerDefinitions.Environment` — ECS sets it automatically when GPU `ResourceRequirements` are declared |
| Task-level `Cpu`/`Memory` errors | Must be **strings** at task level; container uses `MemoryReservation` (integer) |
| `EC2InstanceRole` also fails | Usually caused by `GpuTaskDefinition` failing first (cascade); fix the task def first |

### Cloud job exits during `[Batch Julia]` simulation

If CloudWatch shows a Python traceback ending with `subprocess.CalledProcessError`
for a command like:

```text
julia --threads=1 -O3 .../camrie_tools/simulate_batch.jl ...
```

that traceback is only the Python wrapper reporting that Julia exited with code
`1`. The root cause is the Julia error printed immediately before the Python
traceback.

If the line is:

```text
ERROR: Unable to find compatible target in cached code image.
Target 0 (znver3): Rejecting this target due to use of runtime-disabled features
```

then the Julia sysimage contains native code for the build runner CPU instead of
the AWS runtime CPU. Rebuild the images after confirming the Docker `julia`
wrapper includes `--sysimage-native-code=no`. Do not add `cpu_target="generic"`
to `PackageCompiler.create_sysimage` unless you have verified the full image
build still completes; in practice this can make the sysimage build much heavier
on GitHub-hosted runners.

Pull the surrounding log lines first:

```bash
aws logs tail /ecs/camrie-Prod \
  --since 30m \
  --profile nyu --region us-east-1

aws logs tail /ecs/camrie-gpu-Prod \
  --since 30m \
  --profile nyu --region us-east-1
```

For a specific stream, use the stream prefix printed by the test script or AWS
Batch job details. Also download the failure bundle printed by the app; it
contains the sequence, phantom, manifest, and runtime files needed to reproduce
the failing Julia command locally.

If the error only appears with the sysimage, submit a direct Batch debug job with
`CAMRIE_DISABLE_JULIA_SYSIMAGE=1`. Keep `--project=/opt/camrie-julia`; disabling
the project as well would test a different environment.
### GPU task terminates silently (no logs, exit code non-zero)

```bash
# 1. Check stopped reason and exit code
AWS_PAGER="" aws ecs describe-tasks \
  --cluster camrie-app-prod-cluster \
  --tasks <TASK_ARN> \
  --region us-east-1 --profile nyu \
  --query "tasks[0].[stoppedReason,containers[0].exitCode,containers[0].reason]" \
  --output text

# 2. Check CloudWatch directly (logs exist even if ECS console shows nothing)
aws logs tail /ecs/camrie-gpu-Prod \
  --log-stream-name-prefix camrie-gpu/camrie-worker/<TASK_ID> \
  --profile nyu --region us-east-1
```

### GPU task: 20+ min silent, then times out

Verify the image has the correct Julia config:
- `JULIA_CPU_TARGET=generic` (a specific µarch like `haswell` causes full recompilation on a different CPU generation)
- `JULIA_PKG_DISABLE_PKGIMAGES` **must not be set** in the GPU image (it forces recompilation of all packages on every container start)

Also verify the depot mount is correct in the task definition:
- `ContainerPath` must be `/opt/julia-depot-cache` (NOT `/opt/julia-depot`)
- `JULIA_DEPOT_PATH` must be `/opt/julia-depot-cache:/opt/julia-depot`

Mounting the host path directly over `/opt/julia-depot` shadows the image's precompiled packages, causing Julia to silently recompile everything from scratch on every cold start (~30–60 min).

### assignPublicIp error with GPU tasks

EC2 launch type does **not** support `assignPublicIp`. The GPU `run_task()` call in `RunJobLambda` must omit `awsvpcConfiguration.assignPublicIp`. Only the Fargate path includes it.

### CloudMRHub login fails in CI

```bash
gh secret set CLOUDMR_ADMIN_PASSWORD --repo cloudmrhub/CAMRIE-app
```

---

**Questions?** Check:
- GitHub Actions: `cloudmrhub/CAMRIE-app` → **Actions** tab
- CloudFormation: AWS Console → `camrie-app-prod` → **Events**
- ECS tasks: AWS Console → `camrie-app-prod-cluster` → **Tasks**
- GPU logs: CloudWatch log group `/ecs/camrie-gpu-Prod`
- CPU logs: CloudWatch log group `/ecs/camrie-Prod`
