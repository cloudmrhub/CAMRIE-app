## profile 
profile --nyu

## Architecture

- field bucket / dynamodb
- sequence bucket /dynamodb 
- calculate /
    - vertebra
    - 


project-root/
├── fields/
│   └── template.yaml
├── sequences/
│   └── template.yaml
├── ark/
│   └── template.yaml
├── calculate/
│   ├── template.yaml  # The nested template you shared earlier
│   ├── src/
│   │   ├── muscle/
│   │   │   └── Dockerfile  # For RunJobFunction
│   │   └── vertebra/
│   │       └── task.py     # For DownloadDataFunction
└── template.yaml  # The parent template


sam build --profile nyu
sam deploy --stack-name camrie-app  --profile nyu  --capabilities CAPABILITY_IAM CAPABILITY_AUTO_EXPAND
sam deploy --stack-name camrie-app --profile nyu --capabilities CAPABILITY_IAM CAPABILITY_AUTO_EXPAND --resolve-image-repos
