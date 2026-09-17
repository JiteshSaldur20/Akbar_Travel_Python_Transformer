"""Canonical "home" payload and response schemas (provider-agnostic).

The wire format is the company-wide home contract:

    {
      "originCode": "BOM",
      "destinationCode": "DEL",
      "departureDate": "2026-10-01",
      "returnDate": "2026-10-08",
      "cabinClass": "ECONOMY",        # optional
      "currency": "USD",              # optional
      "passengers": [{"passengerType": "ADT", "count": 1}]
    }

msgspec field aliases map those camelCase keys onto pythonic attribute
names, so transformers work with `home.origin` etc.
"""

from __future__ import annotations

from msgspec import Struct, field


class Passenger(Struct, forbid_unknown_fields=True):
    ptc: str = field(name="passengerType")  # ADT, CHD, INF ...
    count: int


class HomeSearchRequest(Struct, forbid_unknown_fields=True):
    origin: str = field(name="originCode")
    destination: str = field(name="destinationCode")
    depart_date: str = field(name="departureDate")  # ISO date, e.g. 2026-10-01
    return_date: str | None = field(name="returnDate", default=None)
    cabin: str | None = field(name="cabinClass", default=None)
    # Optional extras (not required by the home contract; used by some providers)
    currency: str | None = field(default=None)
    promotion_code: str | None = field(default=None)
    passengers: list[Passenger] = field(default=[])


class Fare(Struct, forbid_unknown_fields=True):
    total: float
    currency: str = "USD"
    base: float | None = None
    taxes: float | None = None


class Segment(Struct, forbid_unknown_fields=True):
    origin: str
    destination: str
    depart_at: str  # ISO datetime, e.g. 2026-10-01T08:30:00
    arrive_at: str
    flight_number: str
    carrier: str


class Offer(Struct, forbid_unknown_fields=True):
    offer_id: str
    provider: str
    fares: list[Fare] = []
    segments: list[Segment] = []


class HomeSearchResponse(Struct, forbid_unknown_fields=True):
    offers: list[Offer] = []