import os
import boto3
import logging
from botocore.exceptions import NoCredentialsError, ClientError
from dotenv import load_dotenv

# Class to upload sql dumps to s3. configuration is loaded from .env,
# see the .env.template for reference
load_dotenv()

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

class S3Uploader:
    def __init__(self):
        logger.info("Initializing S3Uploader")
        self.bucket_name = os.getenv("AWS_BUCKET")
        self.s3_client = boto3.client(
            's3',
            aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
            aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
            region_name=os.getenv("AWS_DEFAULT_REGION")
        )
        logger.info("S3Uploader initialized with bucket: %s", self.bucket_name)

    def upload_file(self, local_file: str, s3_key: str):
        logger.info("Uploading file %s to bucket %s with key %s", local_file, self.bucket_name, s3_key)
        try:
            self.s3_client.upload_file(local_file, self.bucket_name, s3_key)
            logger.info("File %s successfully uploaded as %s", local_file, s3_key)
        except (NoCredentialsError, ClientError) as e:
            logger.error("Failed to upload file to S3: %s", e)
            raise Exception(f"Failed to upload file to S3: {e}")