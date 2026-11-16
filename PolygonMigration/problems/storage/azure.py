import logging
from .base import BaseStorageProvider

logger = logging.getLogger(__name__)

class AzureStorageProvider(BaseStorageProvider):
    """
    Azure Blob Storage provider implementation for backward compatibility.
    """

    def __init__(self, account_url: str, container_name: str, tenant_id: str = None, client_id: str = None, username: str = None, password: str = None):
        self.account_url = account_url
        self.container_name = container_name
        self.tenant_id = tenant_id
        self.client_id = client_id
        self.username = username
        self.password = password
        self.blob_service_client = None

        from azure.identity import UsernamePasswordCredential
        from azure.storage.blob import BlobServiceClient
        
        credential = UsernamePasswordCredential(
            tenant_id=self.tenant_id,
            client_id=self.client_id,
            username=self.username,
            password=self.password
        )
        self.blob_service_client = BlobServiceClient(
            account_url=self.account_url,
            credential=credential
        )

    def upload_test_case(self, db_problem_id: int | str, test_number: int, input_data: str, output_data: str) -> bool:
        input_blob_name = f"test_cases/{db_problem_id}/{test_number}"
        output_blob_name = f"test_cases/{db_problem_id}/{test_number}.a"
        try:
            input_client = self.blob_service_client.get_blob_client(container=self.container_name, blob=input_blob_name)
            input_client.upload_blob(input_data.encode('utf-8'), overwrite=True)
            output_client = self.blob_service_client.get_blob_client(container=self.container_name, blob=output_blob_name)
            output_client.upload_blob(output_data.encode('utf-8'), overwrite=True)
            return True
        except Exception as e:
            logger.error("Error uploading test case #%s to Azure: %s", test_number, e)
            raise

    def delete_test_cases(self, db_problem_id: int | str) -> bool:
        prefix = f"test_cases/{db_problem_id}/"
        try:
            container_client = self.blob_service_client.get_container_client(self.container_name)
            blobs = [blob.name for blob in container_client.list_blobs(name_starts_with=prefix)]
            for blob_name in blobs:
                container_client.delete_blob(blob_name)
            return True
        except Exception as e:
            logger.error("Error deleting test cases from Azure for problem %s: %s", db_problem_id, e)
            return False

    def test_connection(self) -> bool:
        try:
            container_client = self.blob_service_client.get_container_client(self.container_name)
            return container_client.exists()
        except Exception as e:
            logger.error("Azure connection test failed: %s", e)
            return False

