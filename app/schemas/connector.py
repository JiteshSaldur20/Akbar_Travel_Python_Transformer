"""Common flight-search payload shared by every provider microservice.

The Java ORBiS mid-system forwards the SAME common payload to each provider's
Python microservice; each microservice converts it into that provider's own
payload. The wire format is the frontend contract:

    {
      "operation": "FLIGHT_SEARCH",
      "from": "MAA",
      "to": "DEL",
      "dateFrom": "2026-11-04",
      "dateTo": "2026-11-11",
      "tripType": "ROUND_TRIP",
      "ADT": 1,
      "CHD": 1,
      "INF": 0,
      "currency": "INR",
      "cabin": "ECONOMY",
      "refundable": null,
      "filters": { "stops": { "min": 0, "max": 1 }, "airlines": [] }
    }
"""

from __future__ import annotations

from msgspec import Struct, field


class Stops(Struct):
    min: int | None = None
    max: int | None = None


class Filters(Struct):
    stops: Stops | None = None
    airlines: list[str] = field(default_factory=list)


class FlightSearchRequest(Struct):
    from_: str = field(name="from")
    to: str
    dateFrom: str
    operation: str | None = None
    dateTo: str | None = None
    tripType: str | None = None
    adt: int = field(name="ADT", default=0)
    chd: int = field(name="CHD", default=0)
    inf: int = field(name="INF", default=0)
    currency: str | None = None
    cabin: str | None = None
    refundable: bool | None = None
    filters: Filters | None = None
    promotionCode: str | None = None