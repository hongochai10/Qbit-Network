"""QBit SDK exceptions."""


class QBitError(Exception):
    """Base exception for all QBit SDK errors."""

    def __init__(self, message: str, code: int = 0):
        super().__init__(message)
        self.code = code


class AuthenticationError(QBitError):
    """Raised when the API returns 401 (missing or invalid token)."""

    def __init__(self, message: str = "authentication required"):
        super().__init__(message, code=401)


class NotFoundError(QBitError):
    """Raised when the API returns 404."""

    def __init__(self, message: str = "resource not found"):
        super().__init__(message, code=404)


class InsufficientBalance(QBitError):
    """Raised when a transfer or stake fails due to insufficient balance."""

    def __init__(self, message: str = "insufficient balance"):
        super().__init__(message, code=400)


class ValidationError(QBitError):
    """Raised when the API rejects input as invalid (400)."""

    def __init__(self, message: str = "validation error"):
        super().__init__(message, code=400)
