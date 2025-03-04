import json
import os
import boto3import json
import boto3
import os
import tempfile

# Initialize S3 client for re-use.
s3 = boto3.client('s3')

def handler(event, context):
    """
    Lambda handler for the CalculationAppFunction.
    
    Expects an input event like:
    {
      "downloadResult": {
        "metadata": {
          "ID": "...",
          "File": "...",
          ...,
          "Location": {
             "URL": "...",
             "Bucket": "jobbucket",
             "Region": "us-east-1",
             "Key": "cloudMR_birdcagecoil.zip"
          }
        }
      }
    }
    """
    
    print("Received event:", json.dumps(event))
    
    # Retrieve the download result from the event.
    download_result = event.get("downloadResult")
    if not download_result:
        return {
            "statusCode": 400,
            "body": json.dumps({"error": "Missing downloadResult in event"})
        }
    
    metadata = download_result.get("metadata")
    if not metadata:
        return {
            "statusCode": 400,
            "body": json.dumps({"error": "Missing metadata in downloadResult"})
        }
    
    # Extract file location details from metadata.
    location = metadata.get("Location")
    if not location:
        return {
            "statusCode": 400,
            "body": json.dumps({"error": "Missing Location in metadata"})
        }
    
    bucket = location.get("Bucket")
    key = location.get("Key")
    if not bucket or not key:
        return {
            "statusCode": 400,
            "body": json.dumps({"error": "Bucket or Key missing in Location"})
        }
    
    # Download the file from S3.
    try:
        response = s3.get_object(Bucket=bucket, Key=key)
        file_content = response['Body'].read()
    except Exception as e:
        error_msg = f"Error downloading file from S3: {str(e)}"
        print(error_msg)
        return {
            "statusCode": 500,
            "body": json.dumps({"error": error_msg})
        }
    
    # Optionally, write the file content to a temporary file if required by your calculations.
    with tempfile.NamedTemporaryFile(delete=False) as tmp_file:
        tmp_file.write(file_content)
        tmp_filename = tmp_file.name
    
    print(f"File downloaded and saved to temporary file: {tmp_filename}")
    
    # Call your calculation routines using the downloaded file.
    # For example, you could import your calculation module and pass tmp_filename.
    # result = your_calculation_module.process(tmp_filename)
    # For demonstration, we simply return the temporary file path and metadata.
    
    return {
        "statusCode": 200,
        "body": json.dumps({
            "message": "Calculation completed successfully.",
            "tempFile": tmp_filename,
            "metadata": metadata
        })
    }


# Initialize AWS clients outside the handler for re-use.
s3 = boto3.client('s3')
dynamodb = boto3.resource('dynamodb')

# Get the DynamoDB table name from environment variables.
MARIE_FIELD_TABLE_NAME = os.environ.get('MARIE_FIELD_TABLE_NAME', 'MarieFieldMetaData')
SEQUENCE_TABLE_NAME = os.environ.get('SEUQUENCE_TABLE_NAME', 'SequenceMetaData')

def handler(event, context):
    """
    Lambda handler triggered by an S3 event.
    
    Expected S3 object (JSON) content:
      {
         "field_id": "cloudMR_birdcagecoil-ismrm25.zip"
      }
      
    DynamoDB item schema (example):
      {
          "ID": {"S": "cloudMR_birdcagecoil-ismrm25.zip"},
          "File": {"S": "cloudMR_birdcagecoil.zip"},
          "Coil": {"S": "birdcage"},
          "B0": {"S": "3T"},
          "Channels": {"S": "1"},
          "Phantom": {"S": "duke"},
          "Version": {"S": "0.2.3"},
          "Description": {"S": "Birdcage single Coil for 3T MRI scanner with Duke Phantom"},
          "Date": {"S": "2024-09-01"},
          "User": {"S": "gianni02"},
          "Location": {"M": {
              "URL": {"S": "https://jobbucket.s3.amazonaws.com/cloudMR_birdcagecoil.zip"},
              "Bucket": {"S": "jobbucket"},
              "Region": {"S": "us-east-1"},
              "Key": {"S": "cloudMR_birdcagecoil.zip"}
          }}
      }
      
    The function returns the full metadata and the nested Location information.
    """
    print("Received event:", json.dumps(event))
    
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
    
    # Get the JSON file from S3.
    
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
    
    
    
    # Extract the sequence
    seq_id = data.get("seq_id")
    
    if not seq_id:
        error_msg = "Uploaded JSON is missing the 'seq_id' field."
        print(error_msg)
        return {
            "statusCode": 400,
            "body": json.dumps({"error": error_msg})
        }
    
    # Query the DynamoDB table using the file id.
    print(f"Querying DynamoDB table: {MARIE_SEQUENCE_TABLE_NAME}")
    table = dynamodb.Table(MARIE_FIELD_TABLE_NAME)
    try:
        response = table.get_item(Key={'ID': field_id})
    except Exception as e:
        error_msg = f"Error querying DynamoDB: {str(e)}"
        print(error_msg)
        return {
            "statusCode": 500,
            "body": json.dumps({"error": error_msg})
        }
    
    item = response.get("Item")
    if not item:
        error_msg = f"No item found in DynamoDB for ID: {field_id}"
        print(error_msg)
        return {
            "statusCode": 404,
            "body": json.dumps({"error": error_msg})
        }
    
    # Extract the Location map from the DynamoDB item.
    location = item.get("Location")
    if not location:
        error_msg = "DynamoDB item is missing 'Location' information."
        print(error_msg)
        return {
            "statusCode": 500,
            "body": json.dumps({"error": error_msg})
        }
        
        
        
        # Extract the field id from the uploaded JSON.
    field_id = data.get("field_id")
    
    if not field_id:
        error_msg = "Uploaded JSON is missing the 'id' field."
        print(error_msg)
        return {
            "statusCode": 400,
            "body": json.dumps({"error": error_msg})
        }
    
    # Query the DynamoDB table using the file id.
    print(f"Querying DynamoDB table: {MARIE_FIELD_TABLE_NAME}")
    table = dynamodb.Table(MARIE_FIELD_TABLE_NAME)
    try:
        response = table.get_item(Key={'ID': field_id})
    except Exception as e:
        error_msg = f"Error querying DynamoDB: {str(e)}"
        print(error_msg)
        return {
            "statusCode": 500,
            "body": json.dumps({"error": error_msg})
        }

    
    
    # Build the output with full metadata and the file location.
    output = {
        "metadata": item,
        "fieldLocation": location
        
    }
    
    print("Output:", json.dumps(output))
    return output
