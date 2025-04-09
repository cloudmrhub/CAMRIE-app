import json
import requests
import boto3
import os
import os
os.environ['CURL_CA_BUNDLE'] = ''
import zipfile
pipelineAPI =os.getenv('PipelineCompleted')
pipelineAPIFailed =os.getenv('PipelineFailed')
pipelinescheduleAPI = os.getenv("PipelineScheduler")

bucket_result=os.getenv("ResultsBucket","result")
bucket_failed=os.getenv("FailedBucket","failed")
application=os.getenv("Application","CAMRIE")

def getHeadersForRequests():
    return {"Content-Type": "application/json","User-Agent": "My User Agent 1.0","From": "theweblogin@iam.com","Host":os.getenv("Host")}


def getHeadersForRequestsWithToken(token):
    headers = getHeadersForRequests()
    headers["Authorization"]= token
    return headers

import uuid

def lambda_handler(event, context):
    # connect to the s3
    s3 = boto3.client("s3")
    # Get the bucket name and file key
    bucket_name = event["Records"][0]["s3"]["bucket"]["name"]
    file_key = event["Records"][0]["s3"]["object"]["key"]
    #save zip  file to local
    #create a random name 
    print("bucket", bucket_name)
    print("file", file_key)
    fj = f"/tmp/{uuid.uuid4()}.zip"
    print("fj", fj)
    s3 = boto3.resource("s3")
    s3.Bucket(bucket_name).download_file(file_key,fj)
    archive = zipfile.ZipFile(fj, 'r')
    J=archive.read('info.json')
    J=json.loads(J)
    token=J["headers"]["options"]["token"]
    print("token", token)
    
    pipelineid=J["headers"]["options"]["pipelineid"]
    print("pipelineid", pipelineid)
    if pipelineid==None:

        alias=J["headers"]["options"]["alias"]
        data2={"application":application,"alias":alias}
        r2=requests.post(pipelinescheduleAPI, data=json.dumps(data2), headers=getHeadersForRequestsWithToken(token))
        R=r2.json()
        pipelineid = R["pipeline"]
        
        #write into to the info.json
    data2={
    "results":f"s3://{bucket_name}/{file_key}",
    "output":f"s3://{bucket_name}/{file_key}",
    "log":"None",
    "options":"None",
    "input":"None"
    }

    
    print("bucket_name",bucket_name)
    print("result bucket is", bucket_result)
    print("failed bucket is", bucket_failed)
    print("sending API to CloudMR",end= " ")
    if bucket_name==bucket_result:
        url=f'{pipelineAPI}/{pipelineid}'
        r2=requests.post(url, data=json.dumps(data2), headers=getHeadersForRequestsWithToken(token),    verify=False )
        print("ok ", end=" ")
    elif bucket_name==bucket_failed:
        url=f'{pipelineAPIFailed}/{pipelineid}'
        r2=requests.post(url, data=json.dumps(data2), headers=getHeadersForRequestsWithToken(token),    verify=False )
        print("failed  ",end=" ")

    print(" task, updated db")
    return True
