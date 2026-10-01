# CAMRIE — Mode 2 (Self-Hosted Compute)

> New user? Follow the step-by-step guide in [`CAMRIE-Mode2-README.md`](../CAMRIE-Mode2-README.md)
> (IAM user, access keys, AWS profile, install, deploy, verify, teardown).

Run CAMRIE k-space simulations in **your own AWS account**. Jobs are still
submitted from the CAMRIE web app; CloudMR Brain forwards them to your worker,
and results come back to the web app as usual. You pay only while a job runs.

## Prerequisites

- Python 3.9+ (`pip install -r worker/requirements.txt` → boto3, requests)
- An AWS CLI profile for your account (`aws configure --profile my-aws-profile`)
  with permission to create CloudFormation, IAM roles, Lambda, API Gateway, ECS, S3 and CloudWatch Logs
- A CloudMR account (the email/password you use on the CAMRIE web app)

The worker is registered to the CloudMR user you log in with. Only that user
sees it in the web app.

## Deploy — pick one

All three do the same thing: create the `camrie-worker-mode2` stack, log in to
CloudMR Brain and register the worker as a `mode_2` computing unit (~3 min).

### 1. GUI

```bash
python worker/manage.py
```

Fill in **AWS Profile**, **Region**, **CloudMR Email**, **CloudMR Password**,
**Worker Alias** and click **Deploy**. The same window has **Status**, **Logs**,
**Costs** and **Teardown**, plus a **Debug Pipelines** tab that lists your recent
CAMRIE jobs.

### 2. Script

Linux / macOS / WSL:

```bash
./scripts/deploy-and-register-mode2.sh --profile my-aws-profile --alias "My Lab Worker"
```

Windows PowerShell:

```powershell
.\scripts\deploy-and-register-mode2.ps1 -Profile my-aws-profile -Alias "My Lab Worker"
# if scripts are blocked:
powershell -ExecutionPolicy Bypass -File .\scripts\deploy-and-register-mode2.ps1 -Profile my-aws-profile
```

Both scripts install boto3/requests if missing and ask for the CloudMR password
with a hidden prompt.

### 3. Python CLI

```bash
python worker/manage.py deploy --profile my-aws-profile --email you@example.com --alias "My Lab Worker"
```

Non-interactive (CI): set `CLOUDMR_EMAIL` and `CLOUDMR_PASSWORD` in the
environment instead of typing them. Avoid `--password` on the command line; it
ends up in shell history.

A successful deploy ends with:

```
[5/5] Registering with CloudMR Brain...
  Registered: My Lab Worker (ID: <computingUnitId>)
```

Then refresh the CAMRIE **Setup** page: your worker appears in the
computing-unit list next to Mode 1. Select it and submit.

Re-running `deploy` updates the stack in place (or skips it if nothing changed)
and re-registers it. CloudMRHub keeps one Mode 2 worker per user per app, so a
second deploy (another region or profile) replaces the first in the web app's
list; the first stack stays up until you tear it down.

## Manage

```bash
python worker/manage.py status   --profile my-aws-profile   # stack, endpoint, /health, running tasks
python worker/manage.py logs     --profile my-aws-profile --follow
python worker/manage.py costs    --profile my-aws-profile
python worker/manage.py teardown --profile my-aws-profile   # deregister + delete everything
```

Settings are saved to `~/.camrie/config.toml` (profile, region, email, alias,
endpoint, worker API key) and pre-fill the GUI next time. The password is never
saved.

## Architecture

```
CAMRIE web app ─► CloudMR Brain ── POST /compute (x-api-key) ──► Your AWS account
                       ▲                                        ┌──────────────────────────┐
                       │                                        │ API Gateway              │
                       │                                        │   ↓                      │
                       │                                        │ Lambda dispatcher        │
                       │                                        │   ↓  ecs:RunTask         │
                       │   result ZIP via presigned PUT URL     │ Fargate task (4 vCPU,    │
                       └────────────────────────────────────────│ 16 GB): app.py + KomaMRI │
                                                                └──────────────────────────┘
```

1. Brain looks up your computing unit and POSTs the job, with presigned GET URLs
   for the sequence / body-model files and a presigned PUT URL for the result.
2. The Lambda checks the API key and starts one Fargate task. Payloads over
   ~7 KB are stashed in the stack's events bucket and passed by reference.
3. The task downloads the inputs, runs the simulation, uploads the result ZIP to
   the presigned URL and exits.

| Resource | Idle cost |
|---|---|
| API Gateway, Lambda | $0 |
| Fargate (4 vCPU / 16 GB, on demand) | $0 — about $0.23/h while a job runs |
| S3 events bucket (7-day expiry) + results bucket | cents |

Image: `public.ecr.aws/r2m7t0q6/cloudmrhub/camrie-fargate:latest` (override with
`CAMRIE_WORKER_IMAGE`). It is the Mode 1 CPU image plus the presigned-upload
`app.py` (`worker/image/Dockerfile`).

## Files

| File | Role |
|---|---|
| `worker/manage.py` | GUI + CLI: deploy/register, status, logs, costs, teardown |
| `worker/deploy/template.yaml` | CloudFormation: API Gateway, Lambda dispatcher, ECS/Fargate, S3 |
| `worker/image/Dockerfile` | Derived worker image |
| `worker/smoke_test.py` | End-to-end test of a deployed worker without Brain |
| `scripts/deploy-and-register-mode2.sh` / `.ps1` | One-command deploy wrappers |

## Troubleshooting

- **Worker not in the web app** — deploy must end with `Registered: …`. Check you
  are logged in to the web app with the same CloudMR email, then hard-refresh Setup.
- **`Registration FAILED (HTTP …)`** — the stack is up; re-run `deploy`.
- **`Login failed`** — wrong CloudMR email/password.
- **`Unable to locate credentials` / wrong account** — check `aws configure list-profiles`
  and pass the right `--profile`.
- **Job stuck in pending** — Fargate cold start plus image pull takes 1–2 min.
  Watch `logs --follow`. A `CannotPullContainerError` is usually transient; resubmit.
- **Job finished but no result in the web app** — in the logs, `Results uploaded via
  presigned URL` means the result went back to CloudMR. If it says it uploaded to the
  stack's own results bucket instead, Brain didn't send a presigned URL for this job.
- **Test without the web app** — `python worker/smoke_test.py --profile my-aws-profile`.
