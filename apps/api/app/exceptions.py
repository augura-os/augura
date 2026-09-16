"""Domain exception rendered as the unified error envelope."""


class ApiError(Exception):
    """Raised anywhere in the stack; the exception handler in main.py turns
    it into ``{success: false, data: null, message}`` with ``status_code``."""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message
