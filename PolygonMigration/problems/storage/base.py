import logging
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)

class BaseStorageProvider(ABC):
    """
    Abstract interface for Cloud Storage providers in the Polygon Migration Tool.
    
    Any storage provider (Azure Blob, AWS S3, Supabase, Cloudflare R2, Local)
    must implement this interface to be used interchangeably during problem migrations.
    """

    @abstractmethod
    def upload_test_case(self, db_problem_id: int | str, test_number: int, input_data: str, output_data: str) -> bool:
        """
        Uploads a single test case input and output pair to cloud storage.
        
        The expected storage paths are:
            test_cases/{db_problem_id}/{test_number}   (Input file)
            test_cases/{db_problem_id}/{test_number}.a (Output file)
        
        Args:
            db_problem_id: Database problem ID.
            test_number: Test case index (1-based integer).
            input_data: String input content.
            output_data: String output content.
            
        Returns:
            bool: True if upload succeeded, False otherwise.
        """
        pass

    @abstractmethod
    def delete_test_cases(self, db_problem_id: int | str) -> bool:
        """
        Deletes all test cases associated with a given problem ID from storage.
        
        Args:
            db_problem_id: Database problem ID.
            
        Returns:
            bool: True if deletion succeeded, False otherwise.
        """
        pass

    @abstractmethod
    def test_connection(self) -> bool:
        """
        Tests if connection to the storage provider is healthy and credentials are valid.
        
        Returns:
            bool: True if connected, False otherwise.
        """
        pass

