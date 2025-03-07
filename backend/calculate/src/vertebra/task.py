import json
import os
import boto3


import time
import datetime

format="%d/%m/%Y, %H:%M:%S"
def get_time():
    return datetime.datetime.now().strftime(format)


def query_dynamodb(table_name, id_value, id_type):
    """General function to query DynamoDB and return item and location"""
    print(f"Querying DynamoDB table for {id_type}: {table_name}")
    table = dynamodb.Table(table_name)
    
    try:
        response = table.get_item(Key={'ID': id_value})
    except Exception as e:
        error_msg = f"Error querying DynamoDB for {id_type}: {str(e)}"
        print(error_msg)
        return None, {
            "statusCode": 500,
            "body": json.dumps({"error": error_msg})
        }
    
    item = response.get("Item")
    if not item:
        error_msg = f"No item found in DynamoDB for {id_type}: {id_value}"
        print(error_msg)
        return None, {
            "statusCode": 404,
            "body": json.dumps({"error": error_msg})
        }
    
    
    return item, None

# Initialize AWS clients outside the handler for re-use.
s3 = boto3.client('s3')
dynamodb = boto3.resource('dynamodb')

# Get the DynamoDB table name from environment variables.
# MARIE_FIELD_TABLE_NAME = os.environ.get('MARIE_FIELD_TABLE_NAME', 'MarieFieldMetaData')
# SEQUENCE_TABLE_NAME = os.environ.get('SEQUENCE_TABLE_NAME', 'SequenceMetaData')

MARIE_FIELD_TABLE_NAME = 'MarieFieldMetaData'
SEQUENCE_TABLE_NAME = 'SequenceMetaData'
def handler(event, context=None):
    log=[]
    log.append({"when":get_time(),"what":"starting preprocessing","type":"start"})
    # Extract bucket and key from the S3 event.
    try:
        record = event['Records'][0]
        bucket = record['s3']['bucket']['name']
        key = record['s3']['object']['key']
    except KeyError as e:
        error_msg = f"Error parsing S3 event: {str(e)}"
        print(error_msg)
        return {
            "statusCode": 400,
            "body": json.dumps({"error": error_msg})
        }
    
  
    log.append({"when":get_time(),"what":"read task json position","type":"procedure"})    
    print(f"Reading JSON file from S3: s3://{bucket}/{key}")
    try:
        s3_response = s3.get_object(Bucket=bucket, Key=key)
        content = s3_response['Body'].read().decode('utf-8')
        data = json.loads(content)
        log.append({"when":get_time(),"what":"downloaded task json","type":"procedure"})
    except Exception as e:
        error_msg = f"Error reading or parsing S3 object: {str(e)}"
        print(error_msg)
        log.append({"when":get_time(),"what":"downloaded task json","type":"ERROR","error":error_msg})
        return {
            "statusCode": 500,
            "body": json.dumps(log)
        }
    print("JSON data:", json.dumps(data))
    # Extract the file id from the uploaded JSON.
    field_id = data.get("field_id")
    
    log.append({"when":get_time(),"what":"extracted field id","type":"procedure"})
    seq_id = data.get("sequence_id")
    log.append({"when":get_time(),"what":"extracted sequence id","type":"procedure"})

    # Check if field_id exists
    if not field_id:
        error_msg = "Uploaded JSON is missing the 'field_id' field."
        log.append({"when":get_time(),"what":"extracted field id","type":"ERROR","error":error_msg})
        return {
            "statusCode": 400,
            "body": json.dumps(log)
        }

    # Check if seq_id exists
    if not seq_id:
        error_msg = "Uploaded JSON is missing the 'seq_id' field."
        log.append({"when":get_time(),"what":"extracted sequence id","type":"ERROR","error":error_msg})
        print(error_msg)
        return {
            "statusCode": 400,
            "body": json.dumps(log)
        }

    # Query for field_id
    field_result, field_error = query_dynamodb(MARIE_FIELD_TABLE_NAME, field_id, "ID")
    
    if field_error:
        log.append({"when":get_time(),"what":"queried field id","type":"ERROR","error":field_error})
        return field_error
    log.append({"when":get_time(),"what":"queried field id","type":"procedure"})
    # Query for seq_id
    seq_result, seq_error = query_dynamodb(SEQUENCE_TABLE_NAME, seq_id, "ID")
    if seq_error:
        log.append({"when":get_time(),"what":"queried sequence id","type":"ERROR","error":seq_error})
        return seq_error
    log.append({"when":get_time(),"what":"queried sequence id","type":"procedure"})
    log.append({"when":get_time(),"what":"finished preprocessing","type":"end"})

    # Build the output with nested structure
    output = {
        "files":{
        "field": field_result,
        "sequence": seq_result
        },        
        "job": data,
        "log": log
        
    }
    
    
    print("Output:", json.dumps(output))
    return output

