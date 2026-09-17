"""Home Payload model (provider-independent, defined by the Java backend).

msgspec Struct with camelCase JSON names matching the Java DTO.
"""

import datetime
from typing import Any, Literal

import msgspec

from app.exceptions.integration import UnsupportedOperationError

VALID_TRIP_TYPES = ("ONE_WAY", "ROUND_TRIP", "MULTI_CITY")
SUPPORTED_OPERATION = "FLIGHT_SEARCH"
TripType = Literal["ONE_WAY", "ROUND_TRIP", "MULTI_CITY"]


class StopsFilter(msgspec.Struct):
    """Connection count range filter."""

    min: int | None = None
    max: int | None = None


class Filters(msgspec.Struct):
    """Optional search filters from the frontend."""

    stops: StopsFilter | None = None
    airlines: list[str] = msgspec.field(default_factory=list)


class HomePayload(msgspec.Struct, kw_only=True):
    """Provider-independent flight-search payload sent by Java.

    Mirrors Java's ``com.akbartravels.demo.dto.FlightSearchRequest``.
    Never contains provider-specific fields (PCC, tokens, URLs, secrets).
    """

    operation: str = SUPPORTED_OPERATION
    from_location: str = msgspec.field(name="from")
    to_location: str = msgspec.field(name="to")
    date_from: datetime.date = msgspec.field(name="dateFrom")
    date_to: datetime.date = msgspec.field(name="dateTo")
    trip_type: TripType = msgspec.field(name="tripType")
    adt: int = msgspec.field(name="ADT", default=1)
    chd: int = msgspec.field(name="CHD", default=0)
    inf: int = msgspec.field(name="INF", default=0)
    currency: str
    cabin: str | None = None
    refundable: bool | None = None
    filters: Filters = msgspec.field(default_factory=Filters)

    def validate_operation(self) -> None:
        """Reject operations this connector does not implement."""
        if self.operation != SUPPORTED_OPERATION:
            raise UnsupportedOperationError(
                f"Unsupported operation '{self.operation}'. "
                f"This connector only implements {SUPPORTED_OPERATION}."
            )

    def passengers_by_type(self) -> dict[str, int]:
        """Return passenger quantities, omitting zero-count types."""
        passengers: dict[str, int] = {"ADT": self.adt}
        if self.chd > 0:
            passengers["CHD"] = self.chd
        if self.inf > 0:
            passengers["INF"] = self.inf
        return passengers

    def to_public_dict(self) -> dict[str, Any]:
        """Serialize with the original Java field names."""
        return {
            "operation": self.operation,
            "from": self.from_location,
            "to": self.to_location,
            "dateFrom": self.date_from,
            "dateTo": self.date_to,
            "tripType": self.trip_type,
            "ADT": self.adt,
            "CHD": self.chd,
            "INF": self.inf,
            "currency": self.currency,
            "cabin": self.cabin,
            "refundable": self.refundable,
            "filters": {"stops": self.filters.stops, "airlines": self.filters.airlines},
        }


def validate_home_payload_rules(payload: HomePayload) -> None:
    """Cross-field validation that msgspec cannot express declaratively.

    Raises InvalidHomePayloadError on violation.
    """
    from app.exceptions.integration import InvalidHomePayloadError

    errors: list[str] = []

    for field in ("from_location", "to_location"):
        value = getattr(payload, field).strip()
        if not value:
            errors.append(f"{field} must not be blank")
        elif len(value) > 10:
            errors.append(f"{field} must be a valid station code")

    if payload.adt < 1:
        errors.append("ADT must be at least 1")
    if payload.chd < 0:
        errors.append("CHD must be 0 or greater")
    if payload.inf < 0:
        errors.append("INF must be 0 or greater")

    if not payload.currency.strip():
        errors.append("currency must not be blank")

    if errors:
        raise InvalidHomePayloadError(
            "Invalid Home Payload: " + "; ".join(errors)
        )
