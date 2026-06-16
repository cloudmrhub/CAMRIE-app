# CAMRIE Cloud Architecture

This document describes the current production-oriented CAMRIE backend architecture in this repository. It focuses on what runs where, how jobs move through the system, and which parts keep cloud cost low.

## Summary

CAMRIE uses CloudMR Brain for user-facing API, authentication, upload, and shared S3 buckets. This repository deploys the CAMRIE calculation backend as a SAM nested stack. The calculation backend does not keep simulator machines running all the time. It submits work into AWS Batch:

- Small and default jobs run on AWS Batch Fargate Spot.
- Large jobs, or GPU-eligible user-requested jobs, run on AWS Batch EC2 Spot GPU capacity.
- GPU EC2 capacity has `MinvCpus: 0`, so it scales to zero when no GPU jobs are active.

The long-running simulation happens in Batch. Step Functions and Lambda only launch the job.

## Deployed Stacks

| Layer | Stack or resource | Template | Purpose |
|---|---|---|---|
| Root SAM stack | `camrie-app-prod` | `template.yaml` | Wires CAMRIE to CloudMR Brain, imports shared bucket names, passes image/network parameters to the nested app |
| Calculation nested stack | `camrie-app-prod-CalculationApp-*` | `calculation/template.yaml` | Owns Batch queues, job definitions, router Lambda, Step Functions, IAM roles, and log groups |
| CloudMR Brain stack | `cloudmrhub-brain` | external to this repo | Owns API/auth and exports S3 bucket names used by CAMRIE |
| Container images | ECR repository `camrie-fargate` | `calculation/src/DockerfileFargate`, `calculation/src/DockerfileGpu` | CPU and GPU runtime images |

The root stack imports these bucket names from CloudMR Brain:

- `DataBucketPName`: input data, phantom maps, pulse sequences, and oversized job event JSON.
- `ResultsBucketPName`: successful result ZIPs.
- `FailedBucketPName`: failure bundles with logs, options, and traceback.

## Runtime Flow

```text
User / test script
    |
    v
CloudMR Brain API
    |
    v
CalculationStateMachine
    |
    v
RunJobLambda
    |
    +--> CPU queue: AWS Batch Fargate Spot
    |
    +--> GPU queue: AWS Batch EC2 Spot GPU
             |
             v
        Batch container
             |
             +--> downloads input files from S3
             +--> runs Python + Julia/KomaMRI simulation
             +--> uploads result ZIP to results bucket
             +--> uploads failure bundle to failed bucket on error
```

Important detail: the Step Functions execution ends after `RunJobLambda` submits the Batch job. The Batch job is the long-running compute unit.

## Compute Paths

| Feature | CPU path | GPU path |
|---|---|---|
| AWS service | AWS Batch | AWS Batch |
| Capacity type | Fargate Spot | EC2 Spot |
| Platform | Fargate | EC2 with NVIDIA ECS AMI |
| Image parameter | `FargateImageUri` | `GpuImageUri` |
| Dockerfile | `calculation/src/DockerfileFargate` | `calculation/src/DockerfileGpu` |
| Default resources | 4 vCPU, 16 GB RAM | 4 vCPU, 15 GB RAM, 1 GPU |
| Max capacity parameter | `CpuBatchMaxVcpus`, default `256` | `GpuBatchMaxVcpus`, default `32` |
| Scale-to-zero | Native Fargate behavior | `MinvCpus: 0` on GPU compute environment |
| Logs | `/ecs/camrie-Prod` | `/ecs/camrie-gpu-Prod` |
| Job retries | 3 attempts | 2 attempts |

GPU resources are conditional. If `GpuImageUri` is empty, the GPU queue, GPU job definition, GPU launch template, and GPU compute environment are not deployed.

## Routing Logic

`RunJobLambda` estimates job size before submitting to Batch. The estimate is based on:

- number of slices,
- spin factor,
- spins per voxel,
- voxel count read from the NIfTI header when possible.

Routing rules:

| Condition | Route | Reason tag |
|---|---|---|
| GPU is enabled and estimated spins exceed `AutoGpuSpinThreshold` | GPU Batch queue | `auto-threshold` |
| User requests GPU and estimated spins are at least `ForcedGpuMinSpinThreshold` | GPU Batch queue | `user-request` |
| GPU requested but job is too small | CPU Batch queue | `cpu-default` |
| GPU disabled or not configured | CPU Batch queue | `cpu-default` |
| Default | CPU Batch queue | `cpu-default` |

Current defaults:

| Parameter | Default | Meaning |
|---|---:|---|
| `AutoGpuSpinThreshold` | `5000000` | Jobs larger than this can auto-route to GPU |
| `ForcedGpuMinSpinThreshold` | `1000000` | Minimum size required to honor `use_gpu` |
| `BatchJobTimeoutSeconds` | `3600` | Per-attempt Batch timeout |

This protects cost by keeping small jobs off GPU Spot instances even when `--use-gpu` is passed.

## Container Runtime

Both container images build Julia dependencies and a KomaMRI sysimage at image build time. Runtime Julia commands use the sysimage automatically through a wrapper installed as `julia` and `julia-fast`.

| Image | Sysimage | Runtime |
|---|---|---|
| CPU | `/opt/julia-depot/komamri-sysimage.so` | Python 3.11 + Julia 1.11 + KomaMRI |
| GPU | `/opt/julia-depot/komamri-gpu-sysimage.so` | CUDA 12.6 + Python 3.11 + Julia 1.11 + KomaMRI/CUDA |

The Batch job receives the job event in the `FILE_EVENT` environment variable. If the event is too large for a direct environment override, `RunJobLambda` stores it in S3 under `_camrie_events/...` and passes a pointer instead.

## Cost Controls

The architecture is designed to avoid idle simulator cost:

- CPU work uses Fargate Spot and does not require persistent EC2 capacity.
- GPU work uses Batch-managed EC2 Spot with `MinvCpus: 0`.
- GPU instance types are provided as a list, defaulting to `g4dn.xlarge,g4dn.2xlarge,g5.xlarge`, so Batch can choose available Spot capacity.
- Large jobs can route to GPU automatically, but small GPU requests are forced back to CPU.
- Batch job timeouts prevent runaway simulations.
- Log retention is 30 days.

Expected idle state:

```text
CPU Batch compute environment: ENABLED / VALID
GPU Batch compute environment: ENABLED / VALID
GPU desired vCPUs: 0
Active Batch jobs: none
Running CAMRIE ECS tasks: none
Running Step Functions executions: none
```

## Deployment

GitHub Actions builds and deploys the stack from `.github/workflows/deploy-and-register.yml`.

High-level deployment steps:

1. Build and push the CPU image to ECR.
2. Build and push the GPU image when available.
3. Resolve immutable ECR image digests.
4. Run `sam build`.
5. Run `sam deploy` with image URIs, network parameters, CloudMR Brain stack name, and stage.

Important deploy parameters:

| Parameter | Purpose |
|---|---|
| `FargateImageUri` | Immutable CPU image URI |
| `GpuImageUri` | Immutable GPU image URI; empty disables GPU path |
| `CortexHost` | CloudMR Brain API Gateway host |
| `CloudMRBrainStackName` | Stack name used for S3 bucket imports |
| `SubnetId1`, `SubnetId2` | Subnets used by Batch jobs and GPU instances |
| `SecurityGroupIds` | Security group for Batch jobs and GPU instances |
| `GpuInstanceTypes` | GPU Spot instance families Batch may launch |
| `StageName` | `Prod`, `Dev`, or `Test` |

## Running Tests

CPU test:

```bash
python scripts/run_cloud_test.py \
  --token "$TOKEN" \
  --seq-file /data/ARTICLES/bodymodelscreation/tse_ETL1_T1w.seq \
  --phantom-dir calculation/phantom \
  --b0 1.5 \
  --num-slices 4 \
  --spin-factor 16 \
  --tail-logs \
  --monitor-task
```

GPU test:

```bash
python scripts/run_cloud_test.py \
  --token "$TOKEN" \
  --seq-file /data/ARTICLES/bodymodelscreation/tse_ETL1_T1w.seq \
  --phantom-dir calculation/phantom \
  --b0 1.5 \
  --num-slices 4 \
  --spin-factor 16 \
  --tail-logs \
  --use-gpu \
  --timeout 3600
```

`--use-gpu` requests the GPU path, but the router still checks the job-size threshold before launching GPU capacity.

## Operational Checks

Use the safety checker to confirm nothing strange is running:

```bash
python scripts/check_cloud_safety.py --profile nyu --region us-east-1
```

Use a stricter exit code for automation:

```bash
python scripts/check_cloud_safety.py --profile nyu --region us-east-1 --fail-on-warn
```

Allow a known unrelated instance by name:

```bash
python scripts/check_cloud_safety.py \
  --profile nyu \
  --region us-east-1 \
  --allow-name Cancelit-env-1
```

Manual Batch health check:

```bash
aws batch describe-compute-environments \
  --region us-east-1 \
  --profile nyu \
  --query "computeEnvironments[?contains(computeEnvironmentName,'camrie')].[computeEnvironmentName,state,status,computeResources.type,computeResources.minvCpus,computeResources.desiredvCpus,computeResources.maxvCpus]" \
  --output table
```

Manual EC2 check:

```bash
aws ec2 describe-instances \
  --region us-east-1 \
  --profile nyu \
  --filters Name=instance-state-name,Values=running,pending \
  --query "Reservations[].Instances[].[InstanceId,InstanceType,State.Name,LaunchTime,Tags[?Key=='Name']|[0].Value,Tags[?Key=='aws:batch:compute-environment']|[0].Value,Tags[?Key=='aws:ecs:clusterName']|[0].Value]" \
  --output table
```

## What Is Not Part of CAMRIE

Do not treat every EC2 instance in the account as CAMRIE. CAMRIE GPU instances should be tagged with CAMRIE/Batch metadata, for example:

- `App=CAMRIE`
- `Compute=gpu-ec2-spot`
- `Name=camrie-batch-gpu-Prod`
- AWS Batch compute environment tags

An instance managed by another service, such as Elastic Beanstalk, is outside this architecture even if it appears in the same AWS account.

## Source Map

| File | Role |
|---|---|
| `template.yaml` | Root SAM application and CloudMR Brain integration |
| `calculation/template.yaml` | Calculation backend, AWS Batch, Step Functions, router Lambda, IAM |
| `calculation/src/app.py` | Batch container entry point |
| `calculation/src/DockerfileFargate` | CPU image build |
| `calculation/src/DockerfileGpu` | GPU image build |
| `scripts/run_cloud_test.py` | End-to-end cloud test runner |
| `scripts/check_cloud_safety.py` | Operational safety checker |
| `.github/workflows/deploy-and-register.yml` | CI/CD deployment workflow |
| `DEPLOYMENT_GUIDE.md` | Detailed deployment and troubleshooting runbook |
