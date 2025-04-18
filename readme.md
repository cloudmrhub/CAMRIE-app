# CAMRIE Backend

This repository contains the backend services and infrastructure templates for **CAMRIE (Cloud-Accessible MRI Emulator)**, a cloud-based simulator that enables remote execution of MRI simulations using a web-based interface.

---

## Table of Contents

1. [Overview](#overview)  
2. [Architecture](#architecture)  
3. [Setup Instructions](#setup-instructions)  
4. [Templates Workflow](#templates-workflow)  
5. [APIs & Routes](#apis--routes)  
6. [Services](#services)  
7. [License](#license)  
8. [Versions](#versions)

---

## Overview

**CAMRIE** is a web-based MRI simulator built for rapid and accessible evaluation of new MRI technologies.  
It integrates both a cloud-native backend and a React frontend, enabling simulations from any device.  
Simulations are executed remotely via AWS Lambda functions and stored centrally for further analysis and sharing.

---

## Architecture

The backend is organized into modular stacks using AWS SAM and CloudFormation. The key components are:

- **Fields**: Upload and manage magnetic field configurations  
- **Sequences**: Upload and manage pulse sequence configurations  
- **Calculate**: Run simulations (e.g., vertebra, muscle) via Lambda containers  
- **ARK**: Core infrastructure templates  
- **APIs**:
  - `queue-job-python`: Handles job queuing and execution  
  - `user-authorizer-python`: Manages user authentication and access control  
- **Frontend**: Interfaces with backend to allow interactive simulation setup and result visualization  
- **Amplify**: Manages deployment of the frontend

**Project Structure:**

```
project-root/
├── fields/
│   └── template.yaml
├── sequences/
│   └── template.yaml
├── ark/
│   └── template.yaml
├── calculate/
│   ├── template.yaml  
│   ├── src/
│   │   ├── muscle/
│   │   │   └── Dockerfile  # For RunJobFunction
│   │   └── vertebra/
│   │       └── task.py     # For DownloadDataFunction
├── APIs/
│   ├── template.yaml  
│   ├── queue-job-python/
│   └── user-authorizer-python/
├── frontend/
│   └── template.yaml
├── amplify/
│   └── template.yaml
└── template.yaml  # Root template
```

---

## Setup Instructions

### Prerequisites

- **Python**: 3.8+
- **Docker**: Required for Lambda container builds
- **AWS CLI**: Configure your AWS credentials
- **AWS SAM CLI**: For building and deploying the infrastructure
- **jq**: For JSON parsing (used in helper scripts)

### Installation

1. Clone the repository:
   ```bash
   git clone https://github.com/cloudmrhub/camrie-app.git
   cd camrie-app/backend
   ```

2. Configure AWS CLI with your profile:
   ```bash
   aws configure --profile nyu
   ```

3. Build and deploy with SAM:
   ```bash
   sam build --use-container --profile nyu
   sam deploy \
     --stack-name camrie-app \
     --profile nyu \
     --capabilities CAPABILITY_IAM CAPABILITY_AUTO_EXPAND \
     --resolve-image-repos
   ```

---

## Templates Workflow

Each directory under `backend/` is a standalone SAM application. The root `template.yaml` composes them via `AWS::Serverless::Application`:

- fields/           → field uploads & DynamoDB  
- sequences/        → sequence uploads & DynamoDB  
- ark/              → result‐notification, bucket lifecycle, UpdateJobFunction  
- calculate/        → Step Functions + Lambdas (DownloadData, RunJob)  
- APIs/             → API Gateway + Lambdas (QueueJob, UserAuthorizer)  
- frontend/         → upload/download UI APIs  
- amplify/          → React GUI deployment  

Use `sam build` and `sam deploy` to wire parameters (bucket names, ARNs, endpoints) between them.

---

## APIs & Routes

### Control API (`APIs/template.yaml`)

- POST   /pipeline         → QueueJobFunction (enqueue a new simulation)  
- DELETE /pipeline         → DeleteJobFunction (cancel a job)  
- *secured by* UserAuthorizerFunction (JWT in `Authorization` header)  

Exports:  
  • `${StackName}-QueueJobApi`  
  • `${StackName}-ApiId`  
  • `${StackName}-UserAuthorizerFunctionARN`  

### Frontend API (`frontend/template.yaml`)

Routes for data management & results:

- GET    /readdata           → DataReadFunction  
- GET    /deletedata         → DeleteFileFunction  
- POST   /updatedata         → UpdateFileFunction  
- POST   /uploads            → UploadRequestFunction  
- POST   /uploadinitiate     → UploadInitiateFunction  
- POST   /uploadresultsinitiate → UploadResultsInitiateFunction  
- POST   /uploadfinalize     → UploadFinalizeFunction  
- POST   /uploadresultsfinalize → UploadResultsFinalizeFunction  
- GET    /downloads          → DownloadRequestFunction  
- POST   /unzip              → GetZipFunction  

Exports:  
  • `${StackName}-DataReadApi`  
  • `${StackName}-DeleteFileApi`  
  • `${StackName}-UpdateFileApi`  
  • `${StackName}-UploadRequestApi`  
  • `${StackName}-UploadInitiateApi`  
  • `${StackName}-UploadResultsInitiateApi`  
  • `${StackName}-UploadFinalizeApi`  
  • `${StackName}-UploadResultsFinalizeApi`  
  • `${StackName}-DownloadRequestApi`  
  • `${StackName}-GetZipApi`  
  • `${StackName}-FrontendAPI`  

---

## Services

- S3 buckets & DynamoDB tables for fields & sequences  
- Step Functions for workflow orchestration (`calculate/template.yaml`)  
- Lambda containers for compute (`muscle` Docker) & data prep (`vertebra/task.py`)  
- Notification & UpdateJobFunction in `ark/template.yaml`  
- API Gateway + UsagePlan (`test/usageplan/template.yaml`)  
- React frontend deployed via Amplify (`amplify/template.yaml`)

---

## License

This project is licensed under the [MIT License](LICENSE).

---

## Versions

- `dev`: Nightly development version  
- `v1`: Backend-only infrastructure  
- `v1.1`: Full backend + frontend deployed with Amplify

---

[*Dr. Eros Montin, PhD*](http://me.biodimensional.com)  
**46&2 just ahead of me!**

