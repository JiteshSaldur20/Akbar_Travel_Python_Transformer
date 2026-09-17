"""msgspec-based decoding/validation/encoding helpers.

Pure transformation service: no provider HTTP calls, no provider
response decoding. Pydantic is not used anywhere in the app.
"""

from typing import Any

import msgspec

from app.exceptions.integration import InvalidHomePayloadError


def decode_home_payload(raw: bytes | str) -> Any:
    """Decode a JSON Home Payload body into Python data.

    Raises InvalidHomePayloadError on malformed JSON. Field-level
    validation happens via msgspec.convert against the Struct.
    """
    try:
        return msgspec.json.decode(raw)
    except (msgspec.DecodeError, UnicodeDecodeError) as exc:
        raise InvalidHomePayloadError(
            f"Invalid Home Payload: request body is not valid JSON ({exc})"
        ) from exc


def validate_home_payload(raw: Any) -> Any:
    """Validate untrusted data against the HomePayload Struct.

    Raises InvalidHomePayloadError on validation failure.
    """
    from app.models.home_payload import HomePayload

    if isinstance(raw, dict) and "search" in raw and isinstance(raw["search"], dict):
        raw = raw["search"]

    try:
        return msgspec.convert(raw, HomePayload, strict=False)
    except (msgspec.ValidationError, msgspec.DecodeError, TypeError, ValueError) as exc:
        raise InvalidHomePayloadError(
            f"Invalid Home Payload: {exc}"
        ) from exc


def encode_json(value: Any) -> bytes:
    """Encode a value (typically a Struct) to JSON bytes."""
    return msgspec.json.encode(value)
