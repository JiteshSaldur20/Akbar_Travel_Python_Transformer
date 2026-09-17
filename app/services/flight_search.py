"""Flight-search transformation service.

Receive Home Payload -> msgspec decode/validate -> transform ->
return the Sabre request payload. No provider HTTP calls, no
authentication, no response handling: Java owns those.
"""

from typing import Any

from app.config.settings import Settings
from app.exceptions.integration import (
    ConnectorError,
    TransformationError,
)
from app.logging_config import get_logger
from app.models.home_payload import (
    HomePayload,
    validate_home_payload_rules,
)
from app.transformer import request as request_transformer

logger = get_logger(__name__)


class FlightSearchService:
    """Transforms one FLIGHT_SEARCH Home Payload into a Sabre request."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def transform(self, raw_payload: Any) -> dict[str, Any]:
        """Decode/validate the payload and return the Sabre request body.

        Fully request-driven: every call is decoded and transformed
        independently; no state is retained between requests.
        """
        payload = self._validate(raw_payload)
        payload.validate_operation()

        logger.info(
            "Flight search transformation started: %s -> %s (%s) passengers=%s",
            payload.from_location.strip().upper(),
            payload.to_location.strip().upper(),
            payload.trip_type,
            payload.passengers_by_type(),
        )

        sabre_request = self._transform_request(payload)

        logger.info("Flight search transformation completed")

        return sabre_request

    # ------------------------------------------------------------------ //
    #  Pipeline steps
    # ------------------------------------------------------------------ //

    def _validate(self, raw_payload: Any) -> HomePayload:
        """Decode + validate the Home Payload into a Struct."""
        from app.codec import validate_home_payload

        if isinstance(raw_payload, (bytes, str)):
            from app.codec import decode_home_payload

            raw_payload = decode_home_payload(raw_payload)

        payload = validate_home_payload(raw_payload)
        validate_home_payload_rules(payload)
        return payload

    def _transform_request(
        self, payload: HomePayload
    ) -> dict[str, Any]:
        """Transform the payload into the Sabre request payload."""
        try:
            return request_transformer.transform_request(
                payload, self._settings
            )
        except ConnectorError:
            raise
        except Exception as exc:
            raise TransformationError(
                "Failed to transform the flight-search request"
            ) from exc
