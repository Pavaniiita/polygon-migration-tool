import logging
import boto3
from botocore.exceptions import ClientError
from .base import BaseStorageProvider

logger = logging.getLogger(__name__)

class S3StorageProvider(BaseStorageProvider):
    """
    S3-compatible storage provider implementation.
    Supports AWS S3, Supabase Storage S3, Cloudflare R2, MinIO, and Backblaze B2.
    """

    def __init__(self, endpoint_url: str, access_key_id: str, secret_access_key: str, bucket_name: str, region_name: str = 'us-east-1'):
        self.endpoint_url = endpoint_url
        self.access_key_id = access_key_id
        self.secret_access_key = secret_access_key
        self.bucket_name = bucket_name
        self.region_name = region_name or 'us-east-1'

        session = boto3.session.Session()
        self.client = session.client(
            service_name='s3',
            endpoint_url=self.endpoint_url if self.endpoint_url else None,
            aws_access_key_id=self.access_key_id,
            aws_secret_access_key=self.secret_access_key,
            region_name=self.region_name
        )

    def upload_test_case(self, db_problem_id: int | str, test_number: int, input_data: str, output_data: str) -> bool:
        input_key = f"test_cases/{db_problem_id}/{test_number}"
        output_key = f"test_cases/{db_problem_id}/{test_number}.a"

        try:
            # Upload input file
            self.client.put_object(
                Bucket=self.bucket_name,
                Key=input_key,
                Body=input_data.encode('utf-8'),
                ContentType='text/plain; charset=utf-8'
            )
            logger.info("Uploaded input file to S3: %s", input_key)

            # Upload output file (.a)
            self.client.put_object(
                Bucket=self.bucket_name,
                Key=output_key,
                Body=output_data.encode('utf-8'),
                ContentType='text/plain; charset=utf-8'
            )
            logger.info("Uploaded output file to S3: %s", output_key)
            return True
        except ClientError as e:
            logger.error("Failed to upload test case %s to S3 bucket %s: %s", test_number, self.bucket_name, e)
            raise
        except Exception as e:
            logger.error("Unexpected error uploading test case %s to S3: %s", test_number, e)
            raise

    def delete_test_cases(self, db_problem_id: int | str) -> bool:
        prefix = f"test_cases/{db_problem_id}/"
        try:
            paginator = self.client.get_paginator('list_objects_v2')
            pages = paginator.paginate(Bucket=self.bucket_name, Prefix=prefix)

            objects_to_delete = []
            for page in pages:
                for obj in page.get('Contents', []):
                    objects_to_delete.append({'Key': obj['Key']})

            if objects_to_delete:
                for item in objects_to_delete:
                    self.client.delete_object(Bucket=self.bucket_name, Key=item['Key'])
                logger.info("Deleted %d test case files from S3 for problem %s", len(objects_to_delete), db_problem_id)
            return True
        except ClientError as e:
            logger.error("Failed to delete test cases for problem %s from S3: %s", db_problem_id, e)
            return False

    def test_connection(self) -> bool:
        try:
            self.client.head_bucket(Bucket=self.bucket_name)
            return True
        except ClientError as e:
            logger.error("S3 connection test failed for bucket %s: %s", self.bucket_name, e)
            return False
