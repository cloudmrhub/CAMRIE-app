import json
import boto3
import os
import shutil
from pynico_eros_montin import pynico as pn
import os
from pyable_eros_montin import imaginable as ima
import cmtools.cm2D as cm
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

result = os.getenv("RESULTS_BUCKET", "camrie-results")
failed = os.getenv("FAILED_BUCKET", "camrie-failed")


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
from cmtools import cmaws
# Initialize S3 client for re-use.
import numpy as np
import os
import shutil
from pynico_eros_montin import pynico as pn
def handler(event=None, context=None, s3=None):
    G=pn.GarbageCollector()
    if s3 == None:
        s3 = boto3.resource('s3')
    LOG=pn.Log()
    # Retrieve the download result from the event.
    download_result = event.get("downloadResult")
    
    for l in download_result["log"]:
        LOG.append(l)
    
    if not download_result:
        return {
            "statusCode": 400,
            "body": json.dumps({"error": "Missing downloadResult in event"})
        }
    
    LOG.append("starting muscle calculation")
    
    # Extract file location details from metadata.
    fieldinfo=download_result["files"]["field"]
    field=fieldinfo["Location"]
    LOG.getWhatHappened()
    print(pn.getPackagesVersion(["numpy","SimpleITK","pynico_eros_montin","pyable_eros_montin","cmtools"]))
    LOG.append(f"Downloading file from S3: {field.get('Bucket')}/{field.get('Key')}")
    localfield=cmaws.downloadFileFromS3(bucket_name=field["Bucket"], file_key=field["Key"],s3=s3)
    
    G.append(localfield)
    LOG.append(f"Downloaded field file to {localfield}")
    FIELD=c.readMarieOutput(localfield)    
    OUTPUT="/tmp/a.zip"
    OUTPUT=pn.Pathable(OUTPUT)
    OUTPUT.appendPathRandom()
    OUTPUT.changeFileNameRandom()
    OUTPUT.ensureDirectoryExistence()
    OUTDIR=OUTPUT.getPath()
    OUTPUT=OUTPUT.getPosition()
    G.append(OUTDIR)
    import SimpleITK as sitk        
    B0=FIELD["B0"]
    print(B0)
    NT=1
    GPU=False
    SENS_DIR=pn.Pathable(FIELD["b1m"][0]).getPath()
    desired_spin_resolution = (2e-3, 2e-3, 2e-3)

    NC=sitk.ReadImage(FIELD["NC"])
 
    
    sequenceinfo=download_result["files"]["sequence"]
    sequence=sequenceinfo["Location"]
    LOG.append(f"Downloading file from S3: {sequence.get('Bucket')}/{sequence.get('Key')}")
    localsequence=cmaws.downloadFileFromS3(bucket_name=sequence["Bucket"], file_key=sequence["Key"],s3=s3)
    LOG.append(f"Downloaded sequence file to {localsequence}")
    SL=download_result["job"]["image_plane"]["slice_location"]
    
    data=c.simulate_2D_slice(SL, B0, FIELD["T1"],FIELD["T2"],FIELD["T2star"],FIELD["dW"],FIELD["PD"],desired_spin_resolution,"axial",localsequence,OUTDIR,SENS_DIR,GPU,NT,debug=True)
    # data=np.random.rand(100,100,1)+ np.random.rand(100,100,1)*1j
    data=data.astype(np.complex64)
    OUT=cmaws.cmrOutput(app="CAMRIE")
    OUT.out["info"]={"calculation_time": {"time": 0.9518544673919678, "message": None}, "slices": 1}
    K=ima.Imaginable()
    K.setImageFromNumpy(data)
    OUT.addAble(K,0,"Kspace")
    
    R=cm.cm2DReconRSS()
    R.setSignalKSpace(data)
    R.setNoiseCovariance(sitk.GetArrayFromImage(NC))
    RECON=ima.numpyToImaginable(R.getOutput())
    OUT.addAble(RECON,1,"RSS recon")
    
    R.__class__=cm.cm2DKellmanRSS
    SNR=ima.numpyToImaginable(R.getOutput())
    OUT.addAble(SNR,3,"SNR")
    OUT.exportAndZipResultsToS3(result,s3=s3)
      
    
    
    
    return {
        "statusCode": 200,
        "body": json.dumps({
            "message": "Calculation completed successfully.",
            "log": LOG.getWhatHappened(),

        })
    }
