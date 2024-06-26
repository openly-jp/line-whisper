class TranscriptionFailureError(Exception):
    """Raised when the transcription fails"""
    pass

class TranscriptionTimeoutError(Exception):
    """Raised when the transcription was timeout"""
    pass

class FileSizeError(Exception):
    """Raised when the file size is too large"""
    pass

class FileExtensionError(Exception):
    """Raised when the file extension is not supported"""
    pass

class FileCorruptionError(Exception):
    """Raised when the file is corrupted"""
    pass

class UsageLimitError(Exception):
    """Raised when the usage limit is exceeded"""
    def __init__(self, required_sec):
       self.required_sec = required_sec

