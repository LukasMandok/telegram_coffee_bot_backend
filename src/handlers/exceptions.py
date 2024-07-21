class VerificationException(Exception):
    """Custom exception for verification errors."""
    def __init__(self, message="Verification failed"):
        self.message = message
        super().__init__(self.message)