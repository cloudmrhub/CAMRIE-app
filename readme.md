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
│   ├── template.yaml  
│   ├── src/
│   │   ├── muscle/
│   │   │   └── Dockerfile  # For RunJobFunction
│   │   └── vertebra/
│   │       └── task.py     # For DownloadDataFunction
├── APIs/
│   ├── template.yaml  
│   ├── queue-job-python/   # task lambda
│   ├── user-authorizer-python/ #authorizer
├── frontend/
│   ├── template.yaml  (all the apis to upload, retrieve data and results from the backend to the frontend)
├── amplify/
│   ├── template.yaml   (spin the frontend)
└── template.yaml  # The parent template



```bash
cd backend
sam build --use-container --profile nyu && sam deploy --stack-name camrie-app --profile nyu --capabilities CAPABILITY_IAM CAPABILITY_AUTO_EXPAND --resolve-image-repos
```