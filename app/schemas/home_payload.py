"""Canonical "home" payload and response schemas (provider-agnostic).

Request - the wire format the frontend sends:

    {
      "originCode": "BOM",
      "destinationCode": "DEL",
      "departureDate": "2026-10-01",
      "returnDate": "2026-10-08",
      "cabinClass": "ECONOMY",        # optional
      "currency": "USD",              # optional
      "passengers": [{"passengerType": "ADT", "count": 1}]
    }

Response - the single shape every provider is normalised into, so the frontend
never sees a provider-specific field name:

    {
      "operation": "FLIGHT_SEARCH",
      "searchId": "...",              # stamped by Java ORBiS
      "currency": "INR",
      "results": [ { ...one entry per (journey, fare)... } ]
    }

Every field of :class:`HomeResult` is optional. A provider that does not publish
a field leaves it ``None`` rather than omitting it, so the encoded response has
exactly the same structure whichever provider produced it.

msgspec field aliases map those camelCase keys onto pythonic attribute names
(``from_`` for ``from``, ``adt`` for ``ADT``), so transformers work with
``home.origin`` / ``result.adt`` etc.
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
    passengers: list[Passenger] = field(default_factory=list)


class HomeBaggage(Struct, forbid_unknown_fields=True):
    """Checked-baggage allowance, in the provider's own terms."""

    weight: float | None = None
    unit: str | None = None


class HomePassengerFare(Struct, forbid_unknown_fields=True):
    """Fare for one passenger type.

    The three amounts are per passenger; ``count`` says how many passengers the
    price applies to, so the charged amount for the type is ``total * count``.
    """

    count: int | None = None
    base_fare: float | None = field(name="baseFare", default=None)
    tax: float | None = None
    total: float | None = None


class HomeResult(Struct, forbid_unknown_fields=True):
    """One normalised flight result.

    Provider-specific naming is deliberately absent. The three reference fields
    (``journeyKey``, ``segmentKey``, ``fareAvailabilityKey``) carry whatever
    identifiers the provider needs for the later pricing/booking calls, mapped
    onto a common name.
    """

    id: str | None = None
    provider: str | None = None

    from_: str | None = field(name="from", default=None)
    to: str | None = None

    departureDate: str | None = None
    arrivalDate: str | None = None
    departureTime: str | None = None
    arrivalTime: str | None = None

    airline: str | None = None
    flightNumber: str | None = None
    aircraft: str | None = None

    stops: int | None = None
    duration: str | None = None

    cabin: str | None = None
    bookingClass: str | None = None
    fareBasisCode: str | None = None

    seatsAvailable: int | None = None
    refundable: bool | None = None
    baggage: HomeBaggage | None = None

    adt: HomePassengerFare | None = field(name="ADT", default=None)
    chd: HomePassengerFare | None = field(name="CHD", default=None)
    inf: HomePassengerFare | None = field(name="INF", default=None)

    totalPrice: float | None = None

    journeyKey: str | None = None
    segmentKey: str | None = None
    fareAvailabilityKey: str | None = None
    isSumOfSector: bool | None = None


class HomeSearchResponse(Struct, forbid_unknown_fields=True):
    operation: str = "FLIGHT_SEARCH"
    #: Stamped by Java ORBiS, which owns the search correlation id.
    search_id: str | None = field(name="searchId", default=None)
    currency: str | None = None
    results: list[HomeResult] = field(default_factory=list)
