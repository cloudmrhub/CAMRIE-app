# CAMRIE Mode 2 — Spec for Implementation

> Hand this file to a new Kiro session opened on CAMRIE-app-1.
> Reference implementation: `C:\Users\montie01\PROJECTS\mroptimum-app\worker\`

---

## What Mode 2 is

Mode 2 lets an **external user** run CAMRIE simulations on **their own AWS
account** — their compute, their cost, their data sovereignty — while still
submitting jobs from the CloudMR web GUI.

Mode 1 (what exists today): CloudMRHub pays for compute, Brain routes to
`camrie-app-prod` in account `469266894233`.

Mode 2 (to build): the user deploys infrastructure into their own account via a
one-command script (with a Tkinter GUI), registers it with Brain as a `mode_2`
computing unit, and from then on Brain POSTs jobs to the user's own API Gateway
endpoint.

---

## Architecture (Mode 2)

```text
CloudMR Brain (cloudmrhub-brain)         User's AWS Account
┌───────────────┐  POST /compute    ┌──────────────────────────────┐
│               │  (presigned URLs) │  API Gateway (public HTTPS)   │
│  queue_job    │ ────────────────► │       ↓                       │
│               │                   │  Lambda dispatcher             │
│               │                   │       ↓                       │
│               │                   │  Fargate RunTask (one-shot)   │
│               │ ◄──────────────── │       ↓                       │
│               │  presigned PUT    │  app.py → camrie-tools → done │
└───────────────┘                   └──────────────────────────────┘
```

Key difference from Mode 1: **no cross-account IAM trust**. Brain generates
presigned GET URLs for all input files and a presigned PUT URL for the result ZIP,
bundles them into the job JSON, and POSTs that JSON to the user's API Gateway.
The user's Fargate task downloads inputs and uploads results via those presigned
URLs. No S3 bucket policies, no `sts:AssumeRole`, no external ID dance.

The user's infrastructure costs $0 when idle (API Gateway + Lambda + Fargate with
no running tasks = $0/month).

---

## Reference implementation (mroptimum-app)

All files are at `C:\Users\montie01\PROJECTS\mroptimum-app\worker\`:

| file | size | role |
|---|---|---|
| `manage.py` | 40 KB | **Main entry**: Tkinter GUI + CLI subcommands (deploy/status/logs/costs/teardown) |
| `deploy/template.yaml` | 12 KB | SAM template: API Gateway + Lambda dispatcher + Fargate task def + IAM roles + S3 bucket |
| `main.py` | 9.5 KB | Fargate entry point: receives presigned URLs, downloads, runs solver, uploads |
| `Dockerfile` | 805 B | Mode 2 container (pulls `mrotools` package) |
| `requirements.txt` | 287 B | boto3, requests |
| `docker-compose.yml` | 1.2 KB | Local testing |
| `README.md` | 5.8 KB | User-facing docs |

### What `manage.py` does

**GUI mode** (`python manage.py` with no args): opens a Tkinter window with:
- AWS Profile dropdown (auto-detected from `~/.aws/config`)
- CloudMR Email / Password fields
- Worker Alias text field
- Deploy / Status / Logs / Costs / Teardown buttons
- Output console showing progress

**CLI mode** (subcommands):
```bash
python manage.py deploy   --profile eros --email you@uni.edu --alias "My Lab"
python manage.py status   --profile eros
python manage.py logs     --profile eros --follow
python manage.py costs    --profile eros
python manage.py teardown --profile eros
```

**Config persistence**: saves to `~/.mroptimum/config.toml` after first deploy;
auto-populates the GUI on next launch.

### What `deploy/template.yaml` creates in the user's account

- API Gateway (REST, with API key)
- Lambda dispatcher (validates API key, launches Fargate task)
- ECS Cluster + Fargate Task Definition (pulls from CloudMRHub's **public** ECR)
- IAM roles (task execution + task role with S3 access)
- S3 bucket for local results (optional — can also upload via presigned PUT)
- CloudWatch log group

### What `main.py` does (the Mode 2 Fargate entry)

1. Receives the job JSON via the `FILE_EVENT` env var
2. Downloads each input file via its presigned GET URL
3. Runs the solver
4. Uploads the result ZIP via the presigned PUT URL
5. Optionally notifies Brain of completion via a callback URL
6. Exits (Fargate task stops, cost stops)

---

## What CAMRIE Mode 2 needs (adaptation from mroptimum)

### 1. `worker/manage.py` — port with these changes

- App name: `CAMRIE` not `MR Optimum`
- Config dir: `~/.camrie/` not `~/.mroptimum/`
- Stack name default: `camrie-worker-mode2`
- Image source: `469266894233.dkr.ecr.us-east-1.amazonaws.com/camrie-fargate:latest`
  (or a public ECR mirror — see §Prerequisites)
- The `deploy` subcommand needs an **optional GPU flag** (`--gpu`) that adds the
  GPU compute environment + job definition to the user's stack. Default is CPU-only.
- `costs` should show Fargate Spot pricing, not plain Fargate
- Logo URL for registration: update to CAMRIE's logo

### 2. `worker/deploy/template.yaml` — port with these changes

- Fargate image URI → parameterised, default = CAMRIE public ECR
- Add a `GpuEnabled` condition and GPU resources (mirrors
  `calculation/template.yaml` in this repo) — conditional on a parameter
- Task resources: 4 vCPU / 16 GB RAM (matching Mode 1)
- Environment variables: `ResultsBucketName` / `FailedBucketName` / `CAMRIE_THREADS`
- The Lambda dispatcher validates the API key and passes the full job JSON
  (including presigned URLs) as `FILE_EVENT`

### 3. `worker/main.py` — may not be needed

CAMRIE's existing `calculation/src/app.py` already handles:
- `FILE_EVENT` env var
- Presigned URL downloads (`if "presigned_url" in file_info:`)
- The full pipeline call
- Result packaging via `cmrOutput`

So the Mode 2 Fargate task can use the **same** `app.py` as Mode 1. The only
difference is that inputs arrive as presigned URLs rather than direct S3 keys.
This is already supported — no separate `main.py` needed.

The Mode 2 Docker image can therefore be **the same image** as Mode 1
(`camrie-fargate:latest`), just pulled from a public registry.

### 4. Public ECR — prerequisite

The user's Fargate in their account needs to pull `camrie-fargate`. Options:

a) **Public ECR** (recommended): push to `public.ecr.aws/<alias>/camrie-fargate`.
   Add a step to `build-images.yml` that pushes to public alongside private.

b) **Cross-account ECR pull** via a resource policy on the private repo. Works but
   requires the user to authenticate against your private registry, which defeats
   the simplicity goal.

c) **User builds the image themselves.** Defeats the one-click goal.

Option (a) is what mroptimum-app does. Their build workflow pushes to both
private (`469266894233.dkr.ecr.us-east-1.amazonaws.com/mroptimum-fargate`) and
public (`public.ecr.aws/cloudmrhub/mroptimum-fargate`).

### 5. Registration payload (Mode 2)

```json
{
  "appName": "CAMRIE",
  "mode": "mode_2",
  "provider": "user",
  "awsAccountId": "<user's account>",
  "region": "us-east-1",
  "apiEndpoint": "https://<user's API GW>.execute-api.us-east-1.amazonaws.com/Prod",
  "apiKey": "<generated key>",
  "isDefault": false,
  "alias": "My Lab Worker"
}
```

Note: Mode 2 registers an `apiEndpoint` + `apiKey`, **not** a `stateMachineArn`.
Brain POSTs the job to that endpoint rather than invoking Step Functions.

---

## Implementation order

1. **Create the public ECR repo** and add a push step to `build-images.yml`
2. **Port `manage.py`** — start with CLI only (GUI is Tkinter and ports cleanly but
   is more code to test)
3. **Write `worker/deploy/template.yaml`** — API Gateway + Lambda + Fargate, pulling
   from public ECR
4. **Test locally**: `python manage.py deploy --profile nyu --email ... --alias test`
5. **Add GPU option** to the template (conditional resources)
6. **Port the Tkinter GUI** in `manage.py`
7. **Add to CI**: push to public ECR in `build-images.yml`

---

## Existing CAMRIE code that already supports Mode 2

These are NOT changes — they already work:

- `app.py` line ~194: `if "presigned_url" in file_info:` → downloads via presigned GET
- `app.py` `main()`: reads `FILE_EVENT`, handles the S3 pointer for oversized events
- `app.py` `do_process`: the entire solve-and-package flow
- `DockerfileFargate`: the image, ready to be pushed publicly
- GPU path: conditional on `use_gpu` in the job JSON, so the same container works
  on both CPU-only and GPU Mode 2 deployments

---

## What the user experience looks like (target)

```bash
# Install (one time)
pip install boto3 requests

# Deploy CAMRIE into your own account
python worker/manage.py deploy \
  --profile my-aws-profile \
  --email you@university.edu \
  --alias "Our Lab GPU Cluster" \
  --gpu

# Check status
python worker/manage.py status --profile my-aws-profile

# View logs while a job runs
python worker/manage.py logs --profile my-aws-profile --follow

# Check costs
python worker/manage.py costs --profile my-aws-profile

# Remove everything ($0 after this)
python worker/manage.py teardown --profile my-aws-profile
```

After deploy, the user goes to the CAMRIE web GUI, submits a simulation, and
selects "Our Lab GPU Cluster" from the computing unit dropdown. The job runs on
their infrastructure.

---

## Files that will be added to CAMRIE-app

```text
worker/
├── manage.py                 CLI + Tkinter GUI
├── deploy/
│   └── template.yaml         Mode 2 SAM template
├── requirements.txt          boto3, requests
├── docker-compose.yml        local testing
└── README.md                 user-facing docs
```

Plus modifications:
- `.github/workflows/build-images.yml` — add public ECR push step
- `scripts/register-computing-unit.sh` — support both mode_1 and mode_2

---

## Context for the implementing agent

- **mroptimum-app** is at `C:\Users\montie01\PROJECTS\mroptimum-app` — use its
  `worker/` as the direct reference, not documentation
- **CAMRIE-app** is at `C:\Users\montie01\PROJECTS\CAMRIE-app-1`
- Read `CAMRIE-app-1/AGENTS.md` for the full architecture context
- The existing Mode 1 stack is `camrie-app-prod` in account `469266894233`
- ECR private repo: `camrie-fargate` (same account)
- AWS profile: `nyu`
- Docker is available and running
- `sam`, `aws`, `gh` CLI all available
- The Lambda runtime must be `python3.13` (not 3.11 — deprecated, creation
  blocked after 2026-07-31)
