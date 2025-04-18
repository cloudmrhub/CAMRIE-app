from lambda_function import *
import json
import os
import boto3

import pynico_eros_montin.pynico as pn
# possible output from donload
E=pn.Pathable("test/muscle_inputSettings_TRI.json")
# E=pn.Pathable("test/muscle_inputSettings_MC.json")

E=E.readJson()
import cmtools.cmaws as cmaws

        
LOGIN=pn.Pathable('/g/key.json').readJson()
KID=LOGIN['key_id']
KSC=LOGIN['key']
TOK=LOGIN['token']

s3 = cmaws.getS3Resource(KID,KSC,TOK)




handler(E, None,s3=s3)