import os
import logging
from django.conf import settings
from .base import BaseStorageProvider
from .s3 import S3StorageProvider
from .azure import AzureStorageProvider

logger = logging.getLogger(__name__)

_storage_instance = None

def get_storage_provider() -> BaseStorageProvider:
    """
    Factory function to retrieve the configured storage provider singleton.
    Reads provider choice and credentials from Django settings / environment variables.
    
    Supported providers:
        - 's3' (default): Works with AWS S3, Supabase, Cloudflare R2, MinIO, etc.
        - 'azure': Works with Azure Blob Storage.
    """
    global _storage_instance
    if _storage_instance is not None:
        return _storage_instance

    provider_type = getattr(settings, 'STORAGE_PROVIDER', os.getenv('STORAGE_PROVIDER', 's3')).lower().strip()

    if provider_type in ('s3', 'r2', 'supabase', 'minio'):
        endpoint_url = (
            getattr(settings, 'STORAGE_S3_ENDPOINT_URL', None) or
            os.getenv('STORAGE_S3_ENDPOINT_URL') or
            getattr(settings, 'AWS_S3_ENDPOINT_URL', None) or
            os.getenv('AWS_S3_ENDPOINT_URL') or
            os.getenv('S3_ENDPOINT_URL')
        )
        access_key_id = (
            getattr(settings, 'STORAGE_S3_ACCESS_KEY_ID', None) or
            os.getenv('STORAGE_S3_ACCESS_KEY_ID') or
            getattr(settings, 'AWS_ACCESS_KEY_ID', None) or
            os.getenv('AWS_ACCESS_KEY_ID')
        )
        secret_access_key = (
            getattr(settings, 'STORAGE_S3_SECRET_ACCESS_KEY', None) or
            os.getenv('STORAGE_S3_SECRET_ACCESS_KEY') or
            getattr(settings, 'AWS_SECRET_ACCESS_KEY', None) or
            os.getenv('AWS_SECRET_ACCESS_KEY')
        )
        bucket_name = (
            getattr(settings, 'STORAGE_S3_BUCKET_NAME', None) or
            os.getenv('STORAGE_S3_BUCKET_NAME') or
            getattr(settings, 'AWS_S3_BUCKET_NAME', None) or
            os.getenv('AWS_S3_BUCKET_NAME') or
            'test-cases'
        )
        region_name = (
            getattr(settings, 'STORAGE_S3_REGION_NAME', None) or
            os.getenv('STORAGE_S3_REGION_NAME') or
            getattr(settings, 'AWS_REGION', None) or
            os.getenv('AWS_REGION') or
            'us-east-1'
        )

        if not access_key_id or not secret_access_key:
            raise ValueError("AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY (or STORAGE_S3_*) must be configured.")

        _storage_instance = S3StorageProvider(
            endpoint_url=endpoint_url,
            access_key_id=access_key_id,
            secret_access_key=secret_access_key,
            bucket_name=bucket_name,
            region_name=region_name
        )
        logger.info("Initialized S3StorageProvider with bucket '%s'", bucket_name)

    elif provider_type == 'azure':
        account_url = getattr(settings, 'AZURE_STORAGE_ACCOUNT_URL', os.getenv('AZURE_STORAGE_ACCOUNT_URL'))
        container_name = getattr(settings, 'AZURE_CONTAINER_NAME', os.getenv('AZURE_CONTAINER_NAME'))
        tenant_id = getattr(settings, 'AZURE_TENANT_ID', os.getenv('AZURE_TENANT_ID'))
        client_id = getattr(settings, 'AZURE_CLIENT_ID', os.getenv('AZURE_CLIENT_ID'))
        username = getattr(settings, 'AZURE_USERNAME', os.getenv('AZURE_USERNAME'))
        password = getattr(settings, 'AZURE_PASSWORD', os.getenv('AZURE_PASSWORD'))

        _storage_instance = AzureStorageProvider(
            account_url=account_url,
            container_name=container_name,
            tenant_id=tenant_id,
            client_id=client_id,
            username=username,
            password=password
        )
        logger.info("Initialized AzureStorageProvider with container '%s'", container_name)

    else:
        raise ValueError(f"Unsupported storage provider type: '{provider_type}'. Must be 's3' or 'azure'.")

    return _storage_instance

