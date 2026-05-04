import boto3
import json
import os
import sys





bucket_name = "camrie-app-sequencesapp-uoovph67lmx-sequencebucket-d7ix1serybbf"  
region = "us-east-1"  
field_json = "build/seq.json" 
os.makedirs("build", exist_ok=True)


files=[]
base_dir ="/data/PROJECTS/Architecture/CAMRIE-app/data/sequences"

param_map = {
    "T1-Weighted_Spoiled_GRE": {"TE": "10ms",  "TR": "40ms",   "FA": [15]},
    "T1-Weighted_Spin_Echo":    {"TE": "10ms",  "TR": "600ms",  "FA": [90, 180]},
    "T2-Weighted_Spin_Echo":    {"TE": "80ms",  "TR": "4000ms", "FA": [90, 180]},
    "PD-Weighted_Spin_Echo":    {"TE": "10ms",  "TR": "4000ms", "FA": [90, 180]},
}

weighted_seq_names = [
    "PD-Weighted_Spin_Echo.mtrk",
    "PD-Weighted_Spin_Echo.seq",
    "T1-Weighted_Spin_Echo.mtrk",
    "T1-Weighted_Spin_Echo.seq",
    "T1-Weighted_Spoiled_GRE.mtrk",
    "T1-Weighted_Spoiled_GRE.seq",
    "T2-Weighted_Spin_Echo.mtrk",
    "T2-Weighted_Spin_Echo.seq",
]

for name in weighted_seq_names:
    base = name.rsplit(".", 1)[0]
    p = param_map[base]
    _t = "mtrk" if name.endswith(".mtrk") else "pulseq"
    files.append({
        "id":   name,
        "file": os.path.join(base_dir, name),
        "name":base,
        "conf": {
            "Type":        _t,
            "Version":     "0",
            "Description": base.replace("_", " "),
            "Date":        "2025-04-23",
            "User":        "artiga02",
            "TE":          p["TE"],
            "TR":          p["TR"],
            "FA":          p["FA"],
        }
    })

    
    
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
                        "Key": {"S": file_info["location"]["key"]},
                        # wrap TE/TR/FA under a Map for DynamoDB
                        "conf": {"M": {
                            "TE": {"S": file_info["conf"]["TE"]},
                            "TR": {"S": file_info["conf"]["TR"]},
                            "FA": {"L": [
                                {"N": str(fa)} for fa in file_info["conf"]["FA"]
                            ]}
                        }}
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


