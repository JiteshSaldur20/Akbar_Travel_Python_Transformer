"""Sabre connector exceptions (transformation-only service).

Only errors relevant to receiving and transforming the Home Payload.
"""

from typing import Any


class ConnectorError(Exception):
    """Base class for all controlled connector errors.

    Never carries credentials, tokens, or stack traces in its message.
    """

    status_code = 500
    error_code = "CONNECTOR_ERROR"

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


class InvalidHomePayloadError(ConnectorError):
    """The Home Payload received from Java is invalid."""

    status_code = 400
    error_code = "INVALID_HOME_PAYLOAD"


class UnsupportedOperationError(ConnectorError):
    """The requested operation is not implemented by this connector."""

    status_code = 400
    error_code = "UNSUPPORTED_OPERATION"


class TransformationError(ConnectorError):
    """Request transformation failed."""

    status_code = 500
    error_code = "TRANSFORMATION_ERROR"
