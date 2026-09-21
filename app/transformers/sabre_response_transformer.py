"""Sabre Bargain Finder Max response -> canonical home response.

Real BFM shape (trimmed) as a live cert response returns it - the *compact* form,
where a schedule descriptor nests its airports, times and airline and states no
``Time`` dates: those come once per leg from the itinerary group's description:

    {
      "groupedItineraryResponse": {
        "scheduleDescs": [ { "id": 7, "elapsedTime": 175, "stopCount": 0,
                             "departure": { "airport": "BLR", "time": "00:15:00+05:30",
                                            "terminal": "2" },
                             "arrival":   { "airport": "DEL", "time": "03:10:00+05:30",
                                            "terminal": "3" },
                             "carrier": { "marketing": "AI", "marketingFlightNumber": 2758,
                                          "operating": "AI", "operatingFlightNumber": 2758,
                                          "equipment": { "code": "32N" } } } ],
        "legDescs": [ { "id": 1, "elapsedTime": 175, "schedules": [ { "ref": 7 } ] } ],
        "itineraryGroups": [ {
            "groupDescription": { "legDescriptions": [
                { "departureDate": "2026-11-06", "departureLocation": "BLR",
                  "arrivalLocation": "DEL" } ] },
            "itineraries": [ {
            "id": 1, "legs": [ { "ref": 1 } ],
            "pricingInformation": [ { "fare": {
                "totalFare": { "amount": 41268.0, "currency": "INR" },
                "passengerInfoList": [ { "passengerInfo": {
                    "passengerType": "ADT", "nonRefundable": False,
                    "passengerTotalFare": { "totalFare": 20634.0,
                                            "baseFareAmount": 16678.0,
                                            "totalTaxAmount": 3956.0 },
                    "fareComponents": [ { "ref": 1,
                                          "baggageAllowance": { "weight": 25, "unit": "KG" } } ] } } ] } } ]
        } ] } ],
        "fareComponentDescs": [ { "id": 1, "fareBasisCode": "V4O7RBIX",
                                  "cabinCode": "Y", "bookingCode": "V",
                                  "segments": [ { "segment": { "bookingCode": "V" } } ] } ]
      }
    }

The flat shape (``origin``, ``destination``, ``departureTime``,
``marketingCarrier``, ``flightNumber``, ``baseFare``) is read as well, because the
spelling depends on the BFM version the PCC is provisioned for; the compact keys
are tried first.

One home result per (itinerary, priced fare) pair: an itinerary Sabre priced more
than once (a branded-fare family) becomes one result per price.

Field mapping
-------------
====================================  ==========================================================
Home result field                     Sabre source
====================================  ==========================================================
``currency``                          ``itineraries[].pricingInformation[].fare.totalFare.currency``
``from`` / ``to``                     first / last schedule ``departure.airport`` / ``arrival.airport``
                                      (flat shape: ``origin`` / ``destination``)
``departureDate`` / ``departureTime`` the leg's ``groupDescription.legDescriptions[].departureDate``
                                      + the schedule's ``departure.time`` (offset dropped);
                                      flat shape: ``departureTime`` + ``departureDateAdjustment``
``arrivalDate``                       the last leg's date + its ``elapsedTime``, so a red-eye
                                      arrival lands on the next day
``arrivalTime``                       last schedule ``arrival.time`` (flat: ``arrivalTime``)
``airline``                           first schedule ``carrier.marketing`` (flat: ``marketingCarrier.code``)
``flightNumber``                      every resolved schedule's ``carrier.marketingFlightNumber``
``aircraft``                          first schedule ``carrier.equipment.code``
``stops``                             resolved schedule count - 1
``duration``                          sum of the schedules' ``elapsedTime`` (minutes)
``cabin``                             resolved ``fareComponentDescs[].cabinCode``
``bookingClass``                      resolved ``fareComponentDescs[].bookingCode``, else
                                      ``segments[].segment.bookingCode``
``fareBasisCode``                     resolved ``fareComponentDescs[].fareBasisCode``
``seatsAvailable``                    resolved ``fareComponentDescs[].seatsAvailable``
``refundable``                        ``!passengerInfo.nonRefundable``
``baggage``                           ``passengerInfo.fareComponents[].baggageAllowance``
``ADT``/``CHD``/``INF`` count         number of ``passengerInfoList`` entries of that type
``ADT``/``CHD``/``INF`` baseFare      ``passengerInfo.passengerTotalFare.baseFareAmount``
                                      (flat shape: ``baseFare``)
``ADT``/``CHD``/``INF`` tax           ``passengerInfo.passengerTotalFare.totalTaxAmount``
``ADT``/``CHD``/``INF`` total         ``passengerInfo.passengerTotalFare.totalFare``
``totalPrice``                        ``fare.totalFare.amount``
``journeyKey``                        ``itineraries[].id``
``segmentKey``                        the resolved ``scheduleDescs[].id`` of the itinerary
``fareAvailabilityKey``               the resolved ``fareComponentDescs[].id`` of the fare
====================================  ==========================================================

``journeyKey`` / ``segmentKey`` / ``fareAvailabilityKey`` are common names: Sabre
has no field of those names, so its itinerary id, resolved schedule ids and
resolved fare-component ids are carried there. That is what the later pricing and
booking calls need, and it keeps the three reference fields populated for every
provider.

Fields Sabre does not state, left ``None`` for this provider only
-----------------------------------------------------------------
``isSumOfSector`` BFM publishes no "sum of sector" flag.

The BFM body is wrapped in ``groupedItineraryResponse``; a bare body is accepted
too, and both the v5 key (``scheduleDescs``) and the v4 one (``schedules``) are
read, since the key depends on the BFM version the PCC is provisioned for. The
same applies to a leg desc's schedule references, published by live cert BFM as
``schedules`` (an array of ``{"ref": <schedule id>}`` objects) and elsewhere as
``scheduleRef``/``scheduleRefs``/``ScheduleRef`` (see ``_SCHEDULE_REF_KEYS``): a
spelling this mapper did not know resolved to no schedules and surfaced as a
SUCCESS with an empty ``results[]``.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from app.core.exceptions import TransformationError
from app.schemas.home_payload import (
    HomeBaggage,
    HomePassengerFare,
    HomeResult,
    HomeSearchResponse,
)
from app.transformers.base import ResponseTransformer
from app.transformers.common.converters import (
    as_bool,
    as_dict,
    as_int,
    as_list,
    as_text,
    join_text,
    parse_price,
    round_money,
)
from app.transformers.common.dates import (
    duration_between,
    format_minutes,
    parse_iso_datetime,
    split_home_datetime,
)

PROVIDER = "SABRE"

logger = logging.getLogger(__name__)

#: Key of the wrapper Sabre puts around the BFM result body.
_ENVELOPE = "groupedItineraryResponse"

#: Keys a BFM ``legDescs`` entry has used for its schedule references. Live BFM
#: cert responses publish ``schedules`` (an array of ``{"ref": <schedule id>}``
#: objects); ``scheduleRef``, ``scheduleRefs`` and the legacy OTA ``ScheduleRef``
#: are accepted too, because resolving none of them drops every itinerary and
#: would surface as "no flights" rather than as an error.
_SCHEDULE_REF_KEYS = (
    "schedules",
    "scheduleRef",
    "scheduleRefs",
    "ScheduleRef",
    "scheduleRefList",
)

#: Keys inside a schedule-reference object that hold the referenced id.
_REF_VALUE_KEYS = ("ref", "id", "scheduleRef")

#: Spellings a BFM schedule descriptor has used for each flight field. BFM's
#: compact shape nests them (``departure.airport``, ``arrival.airport``,
#: ``carrier.marketing``) and is read first; these are the flat spellings of the
#: other shapes, so neither one loses every field.
_ORIGIN_KEYS = ("origin", "originCode", "departureAirport", "departureAirportCode")
_DESTINATION_KEYS = (
    "destination",
    "destinationCode",
    "arrivalAirport",
    "arrivalAirportCode",
)
_DEPARTURE_TIME_KEYS = (
    "departureTime",
    "departureDateTime",
    "scheduledDepartureTime",
)
_ARRIVAL_TIME_KEYS = ("arrivalTime", "arrivalDateTime", "scheduledArrivalTime")
_FLIGHT_NUMBER_KEYS = ("flightNumber", "flightNo", "number")
_EQUIPMENT_KEYS = ("equipment", "equipmentType", "aircraft", "aircraftType")


class SabreResponseTransformer(ResponseTransformer):
    def to_home_response(self, payload: dict[str, Any]) -> HomeSearchResponse:
        if not isinstance(payload, dict):
            raise TransformationError("Sabre: response payload must be a JSON object")

        grouped = as_dict(payload.get(_ENVELOPE)) or payload

        if "itineraryGroups" not in grouped:
            raise TransformationError(
                "Sabre: response is not a groupedItineraryResponse "
                "(no 'groupedItineraryResponse.itineraryGroups')"
            )

        schedules = as_list(grouped.get("scheduleDescs") or grouped.get("schedules"))
        legs = as_list(grouped.get("legDescs"))
        fare_components = as_list(grouped.get("fareComponentDescs"))

        schedule_index = _index_by(schedules, ("id",))
        leg_index = _index_by(legs, ("id", "ref"))
        fare_component_index = _index_by(fare_components, ("id", "ref"))

        results: list[HomeResult] = []
        itineraries = 0
        sample_schedule: dict[str, Any] | None = None
        sample_fare: dict[str, Any] | None = None

        for group in as_list(grouped.get("itineraryGroups")):
            for itinerary in as_list(as_dict(group).get("itineraries")):
                itineraries += 1

                leg_dates = _leg_dates(group)
                itinerary_schedules = self._ordered_schedules(
                    itinerary, schedule_index, leg_index, leg_dates
                )
                if sample_schedule is None and itinerary_schedules:
                    sample_schedule = itinerary_schedules[0][0]

                if sample_fare is None:
                    sample_fare = _first_fare(itinerary)

                results.extend(
                    self._results_for_itinerary(
                        itinerary, itinerary_schedules, fare_component_index
                    )
                )

        logger.info(
            "Sabre response: %d itineraries, %d schedules, %d legs -> %d home results",
            itineraries,
            len(schedules),
            len(legs),
            len(results),
        )

        if itineraries and not results:
            # Every itinerary was dropped, which is almost always a reference
            # mismatch rather than an empty shop: BFM did return flights, so log
            # the structures the references have to resolve against.
            logger.warning(
                "Sabre: %d itineraries were returned but none mapped to a flight "
                "(unknown reference key?). grouped keys=%s legs=%s schedule ids=%s "
                "first itinerary legs=%s",
                itineraries,
                sorted(grouped.keys()),
                legs,
                sorted(schedule_index.keys()),
                [as_dict(leg).get("ref") for leg in as_list(grouped.get("legs"))]
                or _itinerary_legs(grouped),
            )
        elif results and any(result.from_ is None for result in results):
            # Schedules resolved but carry none of the fields read from them, so
            # the descriptors are shaped differently than this mapper expects.
            _log_unexpected_schedule_shape(
                len(results),
                sample_schedule,
                sample_fare,
                as_dict(legs[0]) if legs else None,
            )

        return HomeSearchResponse(
            currency=self._currency(grouped), results=results
        )

    # -- internals ---------------------------------------------------------

    def _results_for_itinerary(
        self,
        itinerary: dict[str, Any],
        schedules: list[tuple[dict[str, Any], str | None]],
        fare_component_index: dict[str, dict[str, Any]],
    ) -> list[HomeResult]:
        """One result per price of one itinerary.

        ``schedules`` pairs each resolved schedule descriptor with the departure
        date of the leg it belongs to, because BFM's compact descriptors publish
        local times without a date.
        """
        if not schedules:
            return []

        schedule_dicts = [schedule for schedule, _ in schedules]
        first, first_leg_date = schedules[0]
        last, last_leg_date = schedules[-1]

        departure_date, departure_time = _departure(first, first_leg_date)
        arrival_date, arrival_time = _arrival(last, last_leg_date)

        itinerary_id = as_text(itinerary.get("id"))
        segment_key = join_text(
            schedule.get("id") for schedule in schedule_dicts
        )

        results: list[HomeResult] = []

        for pricing in as_list(itinerary.get("pricingInformation")):
            fare = as_dict(as_dict(pricing).get("fare"))
            if not fare:
                continue

            components = self._fare_components(fare, fare_component_index)
            fares_by_type = self._passenger_fares(fare)

            results.append(
                HomeResult(
                    id=f"{itinerary_id}|{len(results)}",
                    provider=PROVIDER,
                    from_=_origin(first),
                    to=_destination(last),
                    departureDate=departure_date,
                    departureTime=departure_time,
                    arrivalDate=arrival_date,
                    arrivalTime=arrival_time,
                    airline=_marketing_carrier(first),
                    flightNumber=join_text(
                        _flight_number(schedule) for schedule in schedule_dicts
                    ),
                    aircraft=_equipment(first),
                    stops=len(schedule_dicts) - 1,
                    duration=_duration(
                        schedule_dicts,
                        _iso_datetime(departure_date, departure_time),
                        _iso_datetime(arrival_date, arrival_time),
                    ),
                    cabin=_cabin(components),
                    bookingClass=_booking_code(components),
                    fareBasisCode=_fare_basis_code(components),
                    seatsAvailable=_seats_available(components),
                    refundable=_refundable(fare),
                    baggage=_baggage(fare),
                    adt=fares_by_type.get("ADT"),
                    chd=fares_by_type.get("CHD"),
                    inf=fares_by_type.get("INF"),
                    totalPrice=_total_price(fare, fares_by_type),
                    journeyKey=itinerary_id,
                    segmentKey=segment_key,
                    fareAvailabilityKey=join_text(
                        component.get("id") or component.get("ref")
                        for component in components
                    ),
                    isSumOfSector=None,   # BFM publishes no such flag
                )
            )

        return results

    # -- reference resolution ---------------------------------------------

    @staticmethod
    def _ordered_schedules(
        itinerary: dict[str, Any],
        schedule_index: dict[str, dict[str, Any]],
        leg_index: dict[str, dict[str, Any]],
        leg_dates: list[str | None],
    ) -> list[tuple[dict[str, Any], str | None]]:
        """Schedules of every leg, in leg order, resolved through ``legDescs``.

        Each schedule is paired with the departure date of its leg, taken from
        the itinerary group's leg descriptions in the same order.
        """
        schedules: list[tuple[dict[str, Any], str | None]] = []

        for position, leg in enumerate(as_list(itinerary.get("legs"))):
            leg_desc = leg_index.get(as_text(as_dict(leg).get("ref")) or "")
            if leg_desc is None:
                continue

            leg_date = leg_dates[position] if position < len(leg_dates) else None

            for schedule_ref in _schedule_refs(leg_desc):
                schedule = schedule_index.get(as_text(schedule_ref) or "")
                if schedule is not None:
                    schedules.append((schedule, leg_date))

        return schedules

    @staticmethod
    def _fare_components(
        fare: dict[str, Any], fare_component_index: dict[str, dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """The fare components of a price, resolved from ``fareComponentDescs``.

        Falls back to the inline per-passenger components when no ``ref``
        resolves, so cabin / booking class are still available when the response
        omits the descriptor array.
        """
        resolved: list[dict[str, Any]] = []
        seen: set[str] = set()

        for ref in as_list(fare.get("fareComponents")):
            _add_component(resolved, seen, fare_component_index, as_dict(ref).get("ref"))

        for passenger in _passengers(fare):
            for ref in as_list(passenger.get("fareComponents")):
                _add_component(
                    resolved, seen, fare_component_index, as_dict(ref).get("ref")
                )

        if resolved:
            return resolved

        for passenger in _passengers(fare):
            resolved.extend(as_list(passenger.get("fareComponents")))

        return resolved

    @staticmethod
    def _passenger_fares(fare: dict[str, Any]) -> dict[str, HomePassengerFare]:
        """Per-passenger-type fares, keyed by ADT / CHD / INF.

        Sabre lists one entry per passenger, so the count is the number of
        entries of that type and the amounts are the per-passenger amounts of the
        first entry (they are identical across passengers of a type).
        """
        counts: dict[str, int] = {}
        amounts_by_type: dict[str, dict[str, Any]] = {}

        for passenger in _passengers(fare):
            passenger_type = as_text(passenger.get("passengerType"))
            if not passenger_type:
                continue
            passenger_type = passenger_type.upper()

            counts[passenger_type] = counts.get(passenger_type, 0) + 1
            amounts_by_type.setdefault(passenger_type, passenger)

        fares_by_type: dict[str, HomePassengerFare] = {}

        for passenger_type, passenger in amounts_by_type.items():
            amounts = as_dict(passenger.get("passengerTotalFare"))

            total = parse_price(amounts.get("totalFare"))
            if total is None:
                total = parse_price(passenger.get("totalFare"))

            fares_by_type[passenger_type] = HomePassengerFare(
                count=counts.get(passenger_type),
                base_fare=parse_price(
                    amounts.get("baseFareAmount") or amounts.get("baseFare")
                ),
                tax=parse_price(amounts.get("totalTaxAmount")),
                total=total,
            )

        return fares_by_type

    @staticmethod
    def _currency(grouped: dict[str, Any]) -> str | None:
        for group in as_list(grouped.get("itineraryGroups")):
            for itinerary in as_list(as_dict(group).get("itineraries")):
                for pricing in as_list(as_dict(itinerary).get("pricingInformation")):
                    currency = as_text(
                        as_dict(
                            as_dict(as_dict(pricing).get("fare")).get("totalFare")
                        ).get("currency")
                    )
                    if currency:
                        return currency

        return None


# --- module-level helpers -------------------------------------------------


def _index_by(
    items: list[Any], keys: tuple[str, ...]
) -> dict[str, dict[str, Any]]:
    """Index objects by their id/ref keys, so references resolve in O(1)."""
    index: dict[str, dict[str, Any]] = {}

    for item in items:
        entry = as_dict(item)
        for key in keys:
            value = as_text(entry.get(key))
            if value is not None:
                index.setdefault(value, entry)

    return index


def _itinerary_legs(grouped: dict[str, Any]) -> list[Any]:
    """The ``legs`` of the first itinerary, for the unresolved-reference log."""
    for group in as_list(grouped.get("itineraryGroups")):
        for itinerary in as_list(as_dict(group).get("itineraries")):
            return as_list(as_dict(itinerary).get("legs"))

    return []


def _first_fare(itinerary: dict[str, Any]) -> dict[str, Any] | None:
    """The first price of an itinerary, for the shape diagnostic."""
    for pricing in as_list(itinerary.get("pricingInformation")):
        fare = as_dict(as_dict(pricing).get("fare"))
        if fare:
            return fare

    return None


def _log_unexpected_schedule_shape(
    result_count: int,
    schedule: dict[str, Any] | None,
    fare: dict[str, Any] | None,
    leg: dict[str, Any] | None = None,
) -> None:
    """Report the keys the schedule descriptors actually carry.

    A schedule whose origin / destination / carrier / equipment keys are not the
    ones this mapper reads produces flights with every schedule-derived field
    null - which reads as a data problem rather than a mapping one. Printing the
    real keys once per response makes the difference visible.
    """
    passenger = next(iter(_passengers(fare)), {}) if fare else {}

    logger.warning(
        "Sabre: %d results mapped but every schedule field is null - the schedule "
        "descriptors do not carry the expected keys. schedule keys=%s leg keys=%s "
        "passenger total-fare keys=%s schedule sample=%s",
        result_count,
        sorted(schedule.keys()) if schedule else None,
        sorted(leg.keys()) if leg else None,
        sorted(as_dict(passenger.get("passengerTotalFare")).keys()),
        _preview(schedule),
    )


def _preview(value: Any, limit: int = 1500) -> str:
    """Repr of a node, truncated so a warning stays readable in the log."""
    text = repr(value)

    return text if len(text) <= limit else f"{text[:limit]}... (truncated)"


def _schedule_refs(leg_desc: dict[str, Any]) -> list[Any]:
    """Schedule references of one ``legDescs`` entry, in any BFM spelling."""
    for key in _SCHEDULE_REF_KEYS:
        value = leg_desc.get(key)
        if value is None:
            continue
        # BFM's scheduleRef is an array, but a single scalar is read as one ref
        # rather than being discarded (``as_list`` would drop it).
        entries = value if isinstance(value, (list, tuple)) else [value]
        return [_ref_value(entry) for entry in entries]

    return []


def _ref_value(entry: Any) -> Any:
    """The referenced id of one schedule reference.

    ``schedules`` holds ``{"ref": 2}`` objects; the other spellings hold the id
    directly, so a scalar is returned unchanged. An object whose id cannot be
    found yields ``None``, which simply fails to resolve.
    """
    if not isinstance(entry, dict):
        return entry

    for key in _REF_VALUE_KEYS:
        if entry.get(key) is not None:
            return entry[key]

    return None


def _add_component(
    target: list[dict[str, Any]],
    seen: set[str],
    index: dict[str, dict[str, Any]],
    ref: Any,
) -> None:
    key = as_text(ref)
    if key is None or key in seen:
        return

    component = index.get(key)
    if component is None:
        return

    seen.add(key)
    target.append(component)


def _passengers(fare: dict[str, Any]) -> list[dict[str, Any]]:
    """The ``passengerInfo`` objects of a price."""
    return [
        as_dict(entry.get("passengerInfo"))
        for entry in as_list(fare.get("passengerInfoList"))
        if as_dict(entry.get("passengerInfo"))
    ]


def _node_text(value: Any) -> str | None:
    """Text of a flight field, whether it is a scalar or a nested node.

    ``{"airport": "BLR"}`` and ``{"code": "32N"}`` both yield their value, while
    a carrier node such as ``{"marketing": "AI", ...}`` yields ``None`` - that one
    needs its own key and is read explicitly.
    """
    direct = as_text(value)
    if direct is not None:
        return direct

    node = as_dict(value)
    for key in ("code", "airport", "airportCode", "iataCode", "value"):
        text = as_text(node.get(key))
        if text is not None:
            return text

    return None


def _flight_field(node: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    """The first present of ``keys`` on a flight node, as text."""
    for key in keys:
        text = _node_text(node.get(key))
        if text is not None:
            return text

    return None


def _origin(schedule: dict[str, Any]) -> str | None:
    """Departure airport of one schedule."""
    return _node_text(as_dict(schedule.get("departure"))) or _flight_field(
        schedule, _ORIGIN_KEYS
    )


def _destination(schedule: dict[str, Any]) -> str | None:
    """Arrival airport of one schedule."""
    return _node_text(as_dict(schedule.get("arrival"))) or _flight_field(
        schedule, _DESTINATION_KEYS
    )


def _marketing_carrier(schedule: dict[str, Any]) -> str | None:
    """Marketing carrier code, from either shape of the descriptor."""
    carrier = as_dict(schedule.get("carrier"))

    return (
        as_text(carrier.get("marketing"))
        or _node_text(schedule.get("marketingCarrier"))
        or _node_text(schedule.get("marketingAirline"))
        or as_text(carrier.get("code"))
        or as_text(schedule.get("carrierCode"))
        or _node_text(schedule.get("carrier"))
        or _node_text(schedule.get("operatingCarrier"))
    )


def _flight_number(schedule: dict[str, Any]) -> str | None:
    """Marketing flight number of one schedule."""
    carrier = as_dict(schedule.get("carrier"))

    return (
        as_text(carrier.get("marketingFlightNumber"))
        or as_text(carrier.get("operatingFlightNumber"))
        or _flight_field(schedule, _FLIGHT_NUMBER_KEYS)
    )


def _equipment(schedule: dict[str, Any]) -> str | None:
    """Equipment code, published under ``carrier.equipment`` in the compact shape."""
    carrier_equipment = _node_text(
        as_dict(as_dict(schedule.get("carrier")).get("equipment"))
    )

    return carrier_equipment or _flight_field(schedule, _EQUIPMENT_KEYS)


def _clock(value: Any) -> str | None:
    """``HH:MM:SS`` of a published time, dropping any UTC offset.

    BFM's compact descriptors state ``"00:15:00+05:30"``; the home contract
    carries the local time without the offset.
    """
    text = as_text(value)
    if text is None:
        return None

    return text.split("+")[0].split("-")[0].strip() or None


def _iso_datetime(date: str | None, time: str | None) -> str | None:
    """``YYYY-MM-DDTHH:MM:SS`` from a date and a time, when both are known."""
    if date is None or time is None:
        return None

    return f"{date}T{time}"


def _departure(
    schedule: dict[str, Any], leg_date: str | None
) -> tuple[str | None, str | None]:
    """Departure date and local time of one leg.

    The compact descriptor states the time only (``departure.time``), so the date
    comes from the itinerary group's leg description. The older shape states the
    full date-time in a ``departureTime``.
    """
    clock = _clock(as_dict(schedule.get("departure")).get("time"))
    if clock is not None:
        return leg_date, clock

    return split_home_datetime(
        _adjusted(
            _flight_field(schedule, _DEPARTURE_TIME_KEYS),
            schedule.get("departureDateAdjustment"),
        )
    )


def _arrival(
    schedule: dict[str, Any], leg_date: str | None
) -> tuple[str | None, str | None]:
    """Arrival date and local time of one leg.

    The compact descriptor states the time only and BFM publishes no arrival
    date, so the date is derived from the leg's own values - its departure date
    and time plus its published ``elapsedTime``. That keeps an overnight arrival
    on the following day rather than on the departure date.
    """
    clock = _clock(as_dict(schedule.get("arrival")).get("time"))
    if clock is None:
        return split_home_datetime(
            _adjusted(
                _flight_field(schedule, _ARRIVAL_TIME_KEYS),
                schedule.get("arrivalDateAdjustment"),
            )
        )

    minutes = as_int(schedule.get("elapsedTime"))
    departure_clock = _clock(as_dict(schedule.get("departure")).get("time"))
    if minutes is None or leg_date is None or departure_clock is None:
        return leg_date, clock

    return _add_minutes(leg_date, departure_clock, minutes), clock


def _add_minutes(date: str, clock: str, minutes: int) -> str | None:
    """The date ``minutes`` after ``clock`` on ``date``."""
    start = parse_iso_datetime(f"{date}T{clock}")
    if start is None:
        return None

    return (start + timedelta(minutes=minutes)).strftime("%Y-%m-%d")


def _leg_dates(group: Any) -> list[str | None]:
    """Departure date of every leg of an itinerary group, in leg order.

    BFM states the requested dates here, once per leg, which is what gives the
    compact schedule descriptors their dates.
    """
    entry = as_dict(group)
    description = as_dict(entry.get("groupDescription"))
    leg_descriptions = as_list(
        description.get("legDescriptions") or entry.get("legDescriptions")
    )

    return [as_text(as_dict(leg).get("departureDate")) for leg in leg_descriptions]


def _duration(
    schedules: list[dict[str, Any]], departure: Any, arrival: Any
) -> str | None:
    """Travel time: the sum of the published ``elapsedTime``, else derived."""
    minutes = [
        as_int(schedule.get("elapsedTime")) for schedule in schedules
    ]
    present = [value for value in minutes if value is not None]

    if present and len(present) == len(schedules):
        return format_minutes(sum(present))

    return duration_between(departure, arrival)


def _cabin(components: list[dict[str, Any]]) -> str | None:
    for component in components:
        cabin = as_text(component.get("cabinCode"))
        if cabin:
            return cabin

        segment_cabin = as_text(
            as_dict(_first_segment(component).get("segment")).get("cabinCode")
        )
        if segment_cabin:
            return segment_cabin

    return None


def _booking_code(components: list[dict[str, Any]]) -> str | None:
    for component in components:
        from_segment = as_text(
            as_dict(_first_segment(component).get("segment")).get("bookingCode")
        )
        if from_segment:
            return from_segment

        # BFM also publishes the booking code on the fare component itself, next
        # to the ``cabinCode`` that is read from there.
        from_component = as_text(component.get("bookingCode"))
        if from_component:
            return from_component

        codes = as_list(component.get("bookingCodeList"))
        if codes:
            return as_text(codes[0])

    return None


def _fare_basis_code(components: list[dict[str, Any]]) -> str | None:
    for component in components:
        basis = as_text(component.get("fareBasisCode"))
        if basis:
            return basis

    return None


def _seats_available(components: list[dict[str, Any]]) -> int | None:
    for component in components:
        seats = as_int(component.get("seatsAvailable"))
        if seats is not None:
            return seats

        segment_seats = as_int(
            as_dict(_first_segment(component).get("segment")).get("seatsAvailable")
        )
        if segment_seats is not None:
            return segment_seats

    return None


def _first_segment(component: dict[str, Any]) -> dict[str, Any]:
    segments = as_list(component.get("segments"))

    return as_dict(segments[0]) if segments else {}


def _refundable(fare: dict[str, Any]) -> bool | None:
    """Refundable is the negation of Sabre's ``nonRefundable`` flag."""
    for passenger in _passengers(fare):
        non_refundable = as_bool(passenger.get("nonRefundable"))
        if non_refundable is not None:
            return not non_refundable

    return None


def _baggage(fare: dict[str, Any]) -> HomeBaggage | None:
    """Baggage allowance in weight terms.

    Sabre can also express the allowance as pieces; the home shape only carries
    weight/unit, so a pieces-only allowance stays ``None`` rather than being
    converted into a guessed weight.
    """
    for passenger in _passengers(fare):
        for component in as_list(passenger.get("fareComponents")):
            allowance = _weight_allowance(
                as_dict(component).get("baggageAllowance")
            )
            if allowance is not None:
                return allowance

        for entry in as_list(passenger.get("baggageInformation")):
            allowance = _weight_allowance(as_dict(entry).get("allowance"))
            if allowance is not None:
                return allowance

    return None


def _weight_allowance(node: Any) -> HomeBaggage | None:
    allowance = as_dict(node)
    weight = parse_price(allowance.get("weight"))
    if weight is None:
        return None

    return HomeBaggage(weight=weight, unit=as_text(allowance.get("unit")))


def _total_price(
    fare: dict[str, Any], fares_by_type: dict[str, HomePassengerFare]
) -> float | None:
    amount = parse_price(as_dict(fare.get("totalFare")).get("amount"))
    if amount is not None:
        return amount

    totals = [
        passenger_fare.total * (passenger_fare.count or 1)
        for passenger_fare in fares_by_type.values()
        if passenger_fare.total is not None
    ]

    return round_money(sum(totals)) if totals else None


def _adjusted(iso_datetime: Any, adjustment_days: Any) -> str | None:
    """Apply a schedule's day adjustment, keeping the ISO date-time shape.

    BFM states an overnight arrival by moving the *time* back and flagging the
    adjustment separately, so the flag has to be added back for the arrival date
    to be right.
    """
    text = as_text(iso_datetime)
    days = as_int(adjustment_days)

    if text is None or not days:
        return text

    shifted = parse_iso_datetime(text)
    if shifted is None:
        return text

    return (shifted + timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S")
