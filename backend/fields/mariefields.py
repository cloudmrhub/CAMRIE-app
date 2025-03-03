import boto3
import json
import os
import sys


bucket_name = "camrie-app-fieldapp-1s07xzfaxwbo0-mariefieldbucket-m5dxtfkfwe54"  
region = "us-east-1"  
marie_field_json = "build/marie_field.json" 
os.makedirs("build", exist_ok=True)
files = [
    {
        "id":"cloudMR_birdcagecoil-ismrm25.zip",
        "file":"/data/PROJECTS/Architecture/lambdaKoma/cloudMR_birdcagecoil.zip",
        "conf":
            {
                "Coil":"birdcage",
                "B0":"3T",
                "Channels":"1",
                "Phantom":"duke",
                "Version":"0.2.3",
                "Description":"Birdcage single Coil for 3T MRI scanner with Duke Phantom",
                "Date":"2024-09-01",
                "User":"gianni02"
            }
    },
    {
        "file":"/data/PROJECTS/Architecture/lambdaKoma/cloudMR_overlap.zip",
        "id":"cloudMR_overlap-ismrm25.zip",
        "conf":
            {
                "Coil":"overlap",
                "B0":"3T",
                "Channels":"16",
                "Phantom":"duke",
                "Version":"0.2.3",
                "Description":"Overlap 16 Channelss Coil for 3T MRI scanner with Duke Phantom",
                "Date":"2024-09-01",
                "User":"gianni02"
            }
    },
    {
        "file":"/data/PROJECTS/Architecture/lambdaKoma/cloudMR_triangularcoil.zip",
        "id":"cloudMR_triangularcoil-ismrm25.zip",
        "conf":
            {
                "Coil":"triangular",
                "B0":"3T",
                "Channels":"1",
                "Phantom":"duke",
                "Version":"0.2.3",
                "Description":"Triangular single Coil for 3T MRI scanner with Duke Phantom",
                "Date":"2024-09-01",
                "User":"gianni02",
            }
    }

     ]
# Step 3: Upload Files
import pynico_eros_montin.pynico as pn

for file in files:

    C=pn.BashIt()
    
        
    N = file["id"]                  
    C.setCommand(f"aws s3 cp {file["file"]} s3://{bucket_name}/{N} --profile nyu")
    C.run()
    print(C.getBashOutput())
    url=f"https://{bucket_name}.s3.{region}.amazonaws.com/{N}"
    file["location"]={"url":url,
                        "bucket":bucket_name,
                        "region":region,
                        "key":N}
    print(f"File '{N}' uploaded successfully to '{url}'.")
        
        

# Transform files into the desired format for DynamoDB batch write
dynamodb_items = {
    "MarieFieldMetaData": [
        {
            "PutRequest": {
                "Item": {
                    "ID": {"S": file_info["id"]},
                    "File": {"S": file_info["file"]},
                            "Coil": {"S": file_info["conf"]["Coil"]},
                            "B0": {"S": file_info["conf"]["B0"]},
                            "Channels": {"S": file_info["conf"]["Channels"]},
                            "Phantom": {"S": file_info["conf"]["Phantom"]},
                            "Version": {"S": file_info["conf"]["Version"]},
                            "Description": {"S": file_info["conf"]["Description"]},
                            "Date": {"S": file_info["conf"]["Date"]},
                            "User": {"S": file_info["conf"]["User"]},
                            "Location": {"M": {
                                "URL": {"S": file_info["location"]["url"]},
                                "Bucket": {"S": file_info["location"]["bucket"]},
                                "Region": {"S": file_info["location"]["region"]},
                                "Key": {"S": file_info["location"]["key"]}
                            }}
                        
                    
                }
            }
        } for file_info in files
    ]
}



    

# Step 4: Save file locations to JSON
try:
    with open(marie_field_json, "w") as f:
        json.dump(dynamodb_items, f)
    print(f"File locations saved to '{marie_field_json}'.")
except Exception as e:
    print(f"Error saving file locations to JSON: {e}")
    
C=pn.BashIt()

C.setCommand(f"aws dynamodb batch-write-item --request-items file://{marie_field_json} --profile nyu")
C.run()
print(C.getBashOutput())


