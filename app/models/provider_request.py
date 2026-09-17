"""Provider request model: Sabre BFM (OTA_AirLowFareSearchRQ) shape.

Only confirmed Sabre fields are modelled. The request body is built as a
plain dict by the transformer (BFM blocks vary too much for a rigid
struct) and wrapped in this Struct for type-safe encoding.
"""

from typing import Any

import msgspec


class SabreFlightSearchRequest(msgspec.Struct):
    """Sabre BFM request body wrapper (OTA_AirLowFareSearchRQ).

    Matches the request shape confirmed by the project's Sabre Air API
    Postman collection and the Java-side FlightSearchParameters.
    """

    OTA_AirLowFareSearchRQ: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        """Return the raw request body ready for encoding."""
        return {"OTA_AirLowFareSearchRQ": self.OTA_AirLowFareSearchRQ}
