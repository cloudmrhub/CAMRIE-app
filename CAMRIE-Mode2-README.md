# CAMRIE Mode 2 — Setup and Teardown

This guide explains how a CloudMRHub user can deploy a CAMRIE **Mode 2** worker in their own AWS account.

Mode 2 creates an API Gateway endpoint, a dispatcher Lambda, and on-demand Fargate compute in the user's AWS account. The Fargate task runs only when a k-space simulation job is submitted, so the idle cost is $0. Docker is not required locally: the deployment uses the public CAMRIE container image `public.ecr.aws/r2m7t0q6/cloudmrhub/camrie-fargate:latest`.

## 1. Requirements

### All operating systems

- A working CloudMRHub email and password
- An AWS account
- A dedicated AWS IAM user with Mode 2 deployment permissions
- Git
- Python 3.9 or newer
- AWS CLI v2
- Internet access
- An AWS VPC with a subnet that provides outbound internet access (the default VPC works)

### Windows

- Windows 10 or 11
- PowerShell
- Python available through `py` or `python`

### macOS

- Terminal
- Python available through `python3`
- Git and AWS CLI v2

### Linux

- A shell such as Bash
- Python 3 with `venv` and `pip`
- Git and AWS CLI v2

## 2. Credentials: what each one is for

| Credential | Used where | Save it? |
|---|---|---|
| CloudMRHub email | Deployment command and CloudMRHub login | Yes |
| CloudMRHub password | Hidden deployment/teardown prompt | Use a password manager; never put it in the command |
| AWS console username/password | AWS website only | Store securely |
| AWS Access Key ID | AWS CLI profile | Save securely |
| AWS Secret Access Key | AWS CLI profile | Save securely; AWS shows it only once |
| Worker API key | Generated automatically | Saved by the manager in `~/.camrie/config.toml`; do not share it |

AWS console credentials and AWS CLI access keys are separate. Creating a console password does not create an Access Key ID.

## 3. Create the AWS IAM user

An AWS administrator should:

1. Open **AWS Console → IAM → Users → Create user**.
2. Create a dedicated user such as `camrie-mode2-user`.
3. Enable console access only if the user needs the AWS website.
4. Assign the permissions required to deploy Mode 2.

The deployment user must be able to:

- call `sts:GetCallerIdentity`;
- discover VPCs and subnets with EC2 `Describe*` operations;
- create, update, inspect, and delete CloudFormation stacks;
- create the stack's IAM roles and policies and call `iam:PassRole`;
- provision Lambda, API Gateway, ECS/Fargate, EC2 security groups, S3, and CloudWatch Logs resources;
- empty and delete the stack's S3 buckets (needed by teardown).

The repository does not contain a tested least-privilege deployer policy. Use a dedicated, administrator-approved deployment policy. Never use AWS root credentials.

## 4. Create the Access Key ID and Secret Access Key

1. Open **AWS Console → IAM → Users**.
2. Select the Mode 2 user.
3. Open **Security credentials**.
4. Under **Access keys**, select **Create access key**.
5. Choose **Command Line Interface (CLI)**.
6. Complete the acknowledgement.
7. Create the key and download the CSV.

The secret access key is displayed only once. Do not send the CSV through email or chat, and never commit it to GitHub.

## 5. Create the AWS CLI profile

Run this on Windows, macOS, or Linux:

```bash
aws configure --profile camrie
```

Enter:

```text
AWS Access Key ID: <ACCESS_KEY_ID>
AWS Secret Access Key: <SECRET_ACCESS_KEY>
Default region name: us-east-1
Default output format: json
```

Verify the identity:

```bash
aws sts get-caller-identity --profile camrie
```

Confirm that the returned account and IAM user are correct before continuing.

AWS normally saves the profile in:

- Windows: `%USERPROFILE%\.aws\credentials`
- macOS/Linux: `~/.aws/credentials`

## 6. Install the CAMRIE worker manager

### Windows PowerShell

```powershell
git clone https://github.com/cloudmrhub/CAMRIE-app.git
Set-Location CAMRIE-app
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r worker/requirements.txt
```

If `Activate.ps1` is blocked, run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once and try again.

### macOS

```bash
git clone https://github.com/cloudmrhub/CAMRIE-app.git
cd CAMRIE-app
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r worker/requirements.txt
```

### Linux

```bash
git clone https://github.com/cloudmrhub/CAMRIE-app.git
cd CAMRIE-app
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r worker/requirements.txt
```

If `venv` is missing on Ubuntu/Debian:

```bash
sudo apt install python3-venv
```

The GUI (optional) needs tkinter. It ships with python.org and conda Python; on Ubuntu/Debian install it with `sudo apt install python3-tk`.

## 7. Deploy Mode 2

Replace the placeholders with the user's CloudMRHub email and a recognizable worker name. Use **one** of the options below; they all run the same deploy.

### Option A — Command line (all operating systems)

```bash
python worker/manage.py deploy --profile camrie --email <CLOUDMR_EMAIL> --alias "<WORKER_NAME>"
```

### Option B — One-command script

Windows PowerShell:

```powershell
.\scripts\deploy-and-register-mode2.ps1 -Profile camrie -Email <CLOUDMR_EMAIL> -Alias "<WORKER_NAME>"
```

If scripts are blocked: `powershell -ExecutionPolicy Bypass -File .\scripts\deploy-and-register-mode2.ps1 -Profile camrie -Email <CLOUDMR_EMAIL> -Alias "<WORKER_NAME>"`

macOS/Linux:

```bash
./scripts/deploy-and-register-mode2.sh --profile camrie --email <CLOUDMR_EMAIL> --alias "<WORKER_NAME>"
```

### Option C — GUI

```bash
python worker/manage.py
```

Fill in **AWS Profile** (`camrie`), **AWS Region**, **CloudMR Email**, **CloudMR Password**, and **Worker Alias**, then click **Deploy**. The same window has **Status**, **Logs**, **Costs**, and **Teardown** buttons, and a **Debug Pipelines** tab that lists recent CAMRIE jobs.

### What happens

The command line asks:

```text
AWS region [us-east-1]:
CloudMR password:
```

Press Enter to accept `us-east-1`, then enter the user's existing **CloudMRHub password**. Do not enter the AWS console password or AWS Secret Access Key here. The password is hidden while typing and is never saved.

The manager:

1. logs in to CloudMRHub;
2. reads AWS credentials from the `camrie` profile;
3. verifies the AWS account;
4. detects the VPC and subnets;
5. deploys the `camrie-worker-mode2` stack (about 3 minutes); and
6. registers the worker with CloudMRHub as a `mode_2` computing unit.

A successful deploy ends with:

```text
[5/5] Registering with CloudMR Brain...
  Registered: <WORKER_NAME> (ID: <computingUnitId>)
  Done! Your CAMRIE Mode 2 worker is ready.
```

## 8. Verify and use Mode 2

```bash
python worker/manage.py status --profile camrie
```

A working deployment reports:

- `CREATE_COMPLETE` (or `UPDATE_COMPLETE`)
- an API Gateway endpoint
- `Health: OK`
- `App: CAMRIE`

Sign in to the CAMRIE web app with the **same CloudMRHub account** used for the deploy, then hard-refresh the **Setup** page. The new worker alias appears in the computing-unit list next to Mode 1. Select it, configure the sequences and body model, and submit.

The first job takes 1–2 extra minutes while Fargate downloads the image. To follow it:

```bash
python worker/manage.py logs --profile camrie --follow
```

The job succeeded when the log shows:

```text
Results uploaded via presigned URL: {...}
Job succeeded. Exiting with 0.
```

The result then appears on the web app's Results page.

## 9. Update or inspect the worker

```bash
python worker/manage.py status --profile camrie              # stack, endpoint, health, running tasks
python worker/manage.py logs   --profile camrie --follow     # job logs
python worker/manage.py costs  --profile camrie              # estimated Fargate cost, last 30 days
python worker/manage.py deploy --profile camrie --email <CLOUDMR_EMAIL> --alias "<WORKER_NAME>"   # update
```

Running `deploy` again updates the stack in place (or reports that it is already up to date) and registers the worker again. If a duplicate entry appears in the computing-unit list, remove the older one.

To test the deployed worker without the web app (no CloudMRHub login needed):

```bash
python worker/smoke_test.py --profile camrie
```

It uploads a small phantom to the worker's own bucket, runs one simulation, and prints `SMOKE TEST PASSED` when a result ZIP with k-space and reconstruction comes back.

## 10. Tear down Mode 2

Use the manager instead of deleting the stack directly in AWS. The manager first removes the worker from CloudMRHub, empties the stack's buckets, and then deletes the AWS resources.

```bash
python worker/manage.py teardown --profile camrie
```

Confirm with `y` and enter the CloudMRHub password when prompted.

A successful teardown reports:

```text
Deregistered: <WORKER_NAME> (...)
Stack deleted. All costs stopped.
```

The computing-unit list updates when the web application fetches it again. Refresh the CAMRIE page. If deregistration reports a warning, the AWS stack may be deleted while the old worker remains visible and requires manual removal.

## 11. What to keep and remember

- Remember the AWS profile name: `camrie`.
- Keep the CloudMRHub email and password in a password manager.
- Protect the AWS access-key CSV and `~/.aws/credentials`.
- Protect `~/.camrie/config.toml`; it contains the generated worker API key.
- Never commit credentials, passwords, tokens, account configuration, or `.camrie/config.toml` to GitHub.
- Rotate or delete unused IAM access keys.
- Always use `worker/manage.py teardown` so AWS deletion and CloudMRHub deregistration happen together.

## Cost

| Resource | Idle | While a job runs |
|---|---|---|
| API Gateway, Lambda | $0 | fractions of a cent per job |
| Fargate (4 vCPU / 16 GB, on demand) | $0 | about $0.23 per hour |
| S3 (events bucket expires after 7 days) | cents | cents |

A small single-slice job runs in a few minutes; a multi-slice head simulation can take 10–20 minutes.

## Troubleshooting

- **Access key age is blank:** create an access key under the IAM user's **Security credentials**.
- **Unable to locate credentials:** run `aws configure --profile camrie`.
- **Wrong AWS account:** run `aws sts get-caller-identity --profile camrie`.
- **CloudMR login failed:** verify the same credentials in the CloudMRHub web application.
- **`Registration FAILED (HTTP …)`:** the AWS stack is up; run `deploy` again to retry registration.
- **AccessDenied / InsufficientCapabilities:** the IAM user lacks a required deployment permission.
- **No suitable subnet:** provide a VPC subnet with outbound internet access.
- **Worker not in the computing-unit list:** the deploy must end with `Registered: …`, and the web app must be signed in with the same CloudMRHub email. Hard-refresh the Setup page.
- **Job stuck in pending / `CannotPullContainerError`:** the first image pull can be slow or time out; resubmit the job.
- **Job finished but no result in the web app:** check the logs for `Results uploaded via presigned URL`. If the result went to the stack's own bucket instead, CloudMRHub did not send an upload URL for that job; report it.
- **Worker remains visible after teardown:** check whether the output reported successful deregistration, then refresh the web application.

## Sources

- [CAMRIE Mode 2 worker](https://github.com/cloudmrhub/CAMRIE-app/tree/main/worker) (`worker/README.md` has the architecture and file index)
- [MR Optimum Mode 2 guide](https://github.com/cloudmrhub/mroptimum-app/blob/main/MR-Optimum-Mode2-README.md), which this guide follows
- [AWS CLI named profiles](https://docs.aws.amazon.com/cli/latest/reference/configure/)
- [AWS IAM-user CLI credentials](https://docs.aws.amazon.com/cli/v1/userguide/cli-authentication-user.html)
