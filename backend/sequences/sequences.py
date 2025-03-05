import boto3
import json
import os
import sys





bucket_name = "camrie-app-sequencesapp-uoovph67lmx-sequencebucket-d7ix1serybbf"  
region = "us-east-1"  
field_json = "build/seq.json" 
os.makedirs("build", exist_ok=True)
files = [
    {
        "id":"ISMRM25-miniflash-00.seq",
        "file":"/data/PROJECTS/Architecture/lambdaKoma/pipeline/sdl_miniflash.seq",
        "conf":
            {   
                
                "Type":"pulseq",
                "Version":"0",
                "Description":"ISMRM25",
                "Date":"2024-09-01",
                "User":"artiga02"
            }
    },
    {
        "id":"ISMRM25-miniflash-01.seq",
        "file":"/data/PROJECTS/Architecture/lambdaKoma/pipeline/sdl_pypulseq.seq",
        "conf":
            {
                "Type":"pulseq",
                "Version":"0",
                "Description":"ISMRM25",
                "Date":"2024-09-01",
                "User":"artiga02"
            }
    }
,
    {
        "id":"ISMRM25-t1w.seq",
        "file":"/data/MYDATA/sequences/sdl_pypulseq_TE10_TR600_os2_largeCrush_xSpoil.seq",
        "conf":
            {
                "Type":"pulseq",
                "Version":"0",
                "Description":"ISMRM25",
                "Date":"2024-09-01",
                "User":"artiga02"
            }
    },
    {
        "id":"ISMRM25-t2w.seq",
        "file":"/data/MYDATA/sequences/sdl_pypulseq_TE80_TR4000_os2_largeCrush_xSpoil.seq",
        "conf":
            {
                "Type":"pulseq",
                "Version":"0",
                "Description":"ISMRM25",
                "Date":"2024-09-01",
                "User":"artiga02"
            }
    },
    {
        "id":"ISMRM25-pdw.seq",
        "file":"/data/MYDATA/sequences/sdl_pypulseq_TE10_TR4000_os2_largeCrush_xSpoil.seq",
        "conf":
            {
                "Type":"pulseq",
                "Version":"0",
                "Description":"ISMRM25",
                "Date":"2024-09-01",
                "User":"artiga02"
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
    "SequenceMetaData": [
        {
            "PutRequest": {
                "Item": {
                    "ID": {"S": file_info["id"]},
                    "File": {"S": file_info["file"]},
                            
                            
                            
                            "Type": {"S": file_info["conf"]["Type"]},
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
    with open(field_json, "w") as f:
        json.dump(dynamodb_items, f)
    print(f"File locations saved to '{field_json}'.")
except Exception as e:
    print(f"Error saving file locations to JSON: {e}")
    
C=pn.BashIt()

C.setCommand(f"aws dynamodb batch-write-item --request-items file://{field_json} --profile nyu")
C.run()
print(C.getBashOutput())


