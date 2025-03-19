import json
import requests
import boto3
import os
import certifi

os.environ['CURL_CA_BUNDLE'] = ''

def getHeadersForRequests():
    return {
        "Content-Type": "application/json",
        "User-Agent": 'My User Agent 1.0',
        "From": 'theweblogin@iam.com'
    }

def getHeadersForRequestsWithToken(token):
    headers = getHeadersForRequests()
    headers["Authorization"] = token
    return headers

def lambda_handler(event, context):
    # Parse the request body.
    body = json.loads(event['body'])
    headers = event['headers']
    authorization_header = headers['Authorization']
    
    # Ensure optional fields are present.
    if 'output' not in body:
        body['output'] = None
    if 'task' not in body:
        body['task'] = {}
        
    application = 'CAMRIE'
    alias = body['alias']
    task = body['task']
    output = body['output']
    
    # Call the pipeline scheduler API.
    pipelineAPI = os.environ.get("PipelineScheduler")
    print("Pipeline API:", pipelineAPI)
    
    data2 = {"application": application, "alias": alias}
    r2 = requests.post(pipelineAPI,
                        data=json.dumps(data2),
                        headers=getHeadersForRequestsWithToken(authorization_header),
                        verify=False)
    R = r2.json()
    
    pipeline_id = R["pipeline"]
    
    # Create the job object.
    job = {
        "task": task,
        "output": output,
        "application": application,
        "alias": alias,
        "pipeline": pipeline_id,
        "token": authorization_header,
    }
    
    print("Job object:", job)
    
    # Instead of writing to S3, immediately invoke the state machine.
    sf = boto3.client('stepfunctions')
    state_machine_arn = os.environ.get("CalculationStateMachineARN")
    print("Invoking state machine:", state_machine_arn)
    
    sf_response = sf.start_execution(
        stateMachineArn=state_machine_arn,
        input=json.dumps(job)
    )
    
    print("State machine response:", sf_response)
    
    # Return the response.
    return {
        'statusCode': 200,
        'headers': {
            'Access-Control-Allow-Origin': '*'
        },
        'body': json.dumps({
            'message': 'Successfully started state machine execution',
            'executionArn': sf_response.get("executionArn"),
            'startDate': str(sf_response.get("startDate")),
            'job': job,
            'state_machine_arn': state_machine_arn,
            'state_machine_response': json.dumps(sf_response)
        })
    }
