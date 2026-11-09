"""Private scan photos. Database for local development; S3-compatible for hosting.

Neon Object Storage, Cloudflare R2, AWS S3 and MinIO can use the same adapter.
Original images are never made public. Callers must enforce dairy membership.
"""
import os
import uuid
from abc import ABC, abstractmethod

class ScanImageStore(ABC):
    @abstractmethod
    def store(self,dairy_id:int,batch_id:int,data:bytes,content_type:str)->str|None:...
    @abstractmethod
    def read(self,key:str)->tuple[bytes,str]:...

class S3ScanImageStore(ScanImageStore):
    def __init__(self):
        import boto3
        self.bucket=os.environ['S3_BUCKET']
        self.client=boto3.client('s3',endpoint_url=os.environ.get('S3_ENDPOINT_URL') or None,
          aws_access_key_id=os.environ['S3_ACCESS_KEY_ID'],
          aws_secret_access_key=os.environ['S3_SECRET_ACCESS_KEY'],
          region_name=os.environ.get('S3_REGION','auto'))
    def store(self,dairy_id,batch_id,data,content_type):
        extension='png' if content_type=='image/png' else 'jpg'
        key=f'dairies/{dairy_id}/scans/{batch_id}/{uuid.uuid4().hex}.{extension}'
        self.client.put_object(Bucket=self.bucket,Key=key,Body=data,ContentType=content_type,
              Metadata={'dairy-id':str(dairy_id),'batch-id':str(batch_id)})
        return key
    def read(self,key):
        item=self.client.get_object(Bucket=self.bucket,Key=key)
        return item['Body'].read(),item.get('ContentType','application/octet-stream')

def storage_mode():
    mode=os.getenv('SCAN_IMAGE_STORAGE','database').strip().lower()
    if mode not in {'database','s3'}:raise RuntimeError('SCAN_IMAGE_STORAGE must be database or s3')
    return mode

def get_store()->ScanImageStore|None:
    return S3ScanImageStore() if storage_mode()=='s3' else None
