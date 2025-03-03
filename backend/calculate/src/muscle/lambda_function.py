import json
import boto3
import os
import shutil
from pynico_eros_montin import pynico as pn
import os


def sanitize_for_json(data):
    """Recursively sanitize data to make it JSON serializable."""
    if isinstance(data, dict):
        return {k: sanitize_for_json(v) for k, v in data.items()}
    elif isinstance(data, list):
        return [sanitize_for_json(v) for v in data]
    elif isinstance(data, (int, float, str, bool, type(None))):
        return data
    else:
        return str(data)  # Convert non-serializable types to strings

def write_json_file(file_path, data):
    """
    Sanitizes the data and writes it to a JSON file.
    """
    try:
        sanitized_data = sanitize_for_json(data)
        with open(file_path, 'w', encoding='utf-8') as file:
            json.dump(sanitized_data, file, indent=4)
        print(f"JSON data successfully written to {file_path}")
    except Exception as e:
        print(f"Failed to write JSON data to file: {e}")

mroptimum_result = os.getenv("ResultsBucketName", "mrorv2")
mroptimum_failed = os.getenv("FailedBucketName", "mrofv2")


def s3FileTolocal(J, s3=None, pt="/tmp"):
    key = J["key"]
    bucket = J["bucket"]
    filename = J["filename"]
    if s3 == None:
        s3 = boto3.resource("s3")
    O = pn.Pathable(pt)
    O.addBaseName(filename)
    O.changeFileNameRandom()
    f = O.getPosition()
    s3.Bucket(bucket).download_file(key, f)
    J["filename"] = f
    J["type"] = "local"
    return J


import json
import boto3
import common as c
# Initialize S3 client for re-use.
s3 = boto3.client('s3')

def handler(event=None, context=None):
    """
    Lambda handler for the CalculationAppFunction.
    
    Expects an input event like:
    {
      "downloadResult": {"metadata": {"Date": "2024-09-01", "File": "/data/PROJECTS/Architecture/lambdaKoma/cloudMR_birdcagecoil.zip", "Channels": "1", "Location": {"Region": "us-east-1", "Bucket": "camrie-app-fieldapp-1s07xzfaxwbo0-mariefieldbucket-m5dxtfkfwe54", "URL": "https://camrie-app-fieldapp-1s07xzfaxwbo0-mariefieldbucket-m5dxtfkfwe54.s3.us-east-1.amazonaws.com/cloudMR_birdcagecoil-ismrm25.zip", "Key": "cloudMR_birdcagecoil-ismrm25.zip"}, "Version": "0.2.3", "User": "gianni02", "Phantom": "duke", "Description": "Birdcage single Coil for 3T MRI scanner with Duke Phantom", "ID": "cloudMR_birdcagecoil-ismrm25.zip", "Coil": "birdcage", "B0": "3T"}, "fileLocation": {"Region": "us-east-1", "Bucket": "camrie-app-fieldapp-1s07xzfaxwbo0-mariefieldbucket-m5dxtfkfwe54", "URL": "https://camrie-app-fieldapp-1s07xzfaxwbo0-mariefieldbucket-m5dxtfkfwe54.s3.us-east-1.amazonaws.com/cloudMR_birdcagecoil-ismrm25.zip", "Key": "cloudMR_birdcagecoil-ismrm25.zip"}}
      }
    }
    """
    
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
    
    
    localfn = "/tmp/" + key.split("/")[-1]
    print(f"Downloading file from S3: {bucket}/{key} to {localfn}")
    # Download the file from S3.
    try:
        s3.download_file(Bucket=bucket, Key=key, Filename=localfn)
        print(f"Downloaded file from S3: {bucket}/{key}")
        
    except Exception as e:
        error_msg = f"Error downloading file from S3: {str(e)}"
        print(error_msg)
        return {
            "statusCode": 500,
            "body": json.dumps({"error": error_msg})
        }
    


    FIELD=c.readMarieOutput(localfn)    

    OUTDIR="/tmp/OUTDIR"
    os.makedirs(OUTDIR,exist_ok=True)
    import SimpleITK as sitk        
    B0=FIELD["B0"]
    print(B0)
    NT=20
    GPU=False
    A=pn.Pathable(OUTDIR+'/')
    A.ensureDirectoryExistence()
    SENS_DIR=pn.Pathable(FIELD["b1m"][0]).getPath()
    desired_spin_resolution = (2e-3,2e-3)

    NC=sitk.ReadImage(FIELD["NC"])
    print(sitk.GetArrayFromImage(NC))
    GPU=False
    A=pn.Pathable(OUTDIR+'/')
    A.ensureDirectoryExistence()



    return {
        "statusCode": 200,
        "body": json.dumps({
            "message": "Calculation completed successfully.",
            "tempFile": localfn,
            "metadata": metadata
        })
    }
