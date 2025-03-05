import json
import os
import boto3


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
    
    location = item.get("Location")
    if not location:
        error_msg = f"DynamoDB item is missing 'Location' information for {id_type}."
        print(error_msg)
        return None, {
            "statusCode": 500,
            "body": json.dumps({"error": error_msg})
        }
    
    return {"metadata": item, "fieldLocation": location}, None

# Initialize AWS clients outside the handler for re-use.
s3 = boto3.client('s3')
dynamodb = boto3.resource('dynamodb')

# Get the DynamoDB table name from environment variables.
# MARIE_FIELD_TABLE_NAME = os.environ.get('MARIE_FIELD_TABLE_NAME', 'MarieFieldMetaData')
# SEQUENCE_TABLE_NAME = os.environ.get('SEQUENCE_TABLE_NAME', 'SequenceMetaData')

MARIE_FIELD_TABLE_NAME = 'MarieFieldMetaData'
SEQUENCE_TABLE_NAME = 'SequenceMetaData'
def handler(event, context=None, s3=s3, dynamodb=dynamodb):
     
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
    
  
    
    print(f"Reading JSON file from S3: s3://{bucket}/{key}")
    try:
        s3_response = s3.get_object(Bucket=bucket, Key=key)
        content = s3_response['Body'].read().decode('utf-8')
        data = json.loads(content)
    except Exception as e:
        error_msg = f"Error reading or parsing S3 object: {str(e)}"
        print(error_msg)
        return {
            "statusCode": 500,
            "body": json.dumps({"error": error_msg})
        }
    print("JSON data:", json.dumps(data))
    # Extract the file id from the uploaded JSON.
    field_id = data.get("field_id")
    seq_id = data.get("sequence_id")

    # Check if field_id exists
    if not field_id:
        error_msg = "Uploaded JSON is missing the 'field_id' field."
        print(error_msg)
        return {
            "statusCode": 400,
            "body": json.dumps({"error": error_msg})
        }

    # Check if seq_id exists
    if not seq_id:
        error_msg = "Uploaded JSON is missing the 'seq_id' field."
        print(error_msg)
        return {
            "statusCode": 400,
            "body": json.dumps({"error": error_msg})
        }

    # Query for field_id
    field_result, field_error = query_dynamodb(MARIE_FIELD_TABLE_NAME, field_id, "ID")
    if field_error:
        return field_error

    # Query for seq_id
    seq_result, seq_error = query_dynamodb(SEQUENCE_TABLE_NAME, seq_id, "ID")
    if seq_error:
        return seq_error

    # Build the output with nested structure
    output = {
        "field": field_result,
        "seq": seq_result
    }
    
    
    print("Output:", json.dumps(output))
    return output

