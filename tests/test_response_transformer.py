"""Tests for the Sabre response transformer (BFM -> canonical home response)."""

import pytest

from app.core.exceptions import TransformationError
from app.schemas.home_payload import HomeSearchResponse
from app.transformers.sabre_response_transformer import SabreResponseTransformer


def test_leg_desc_schedule_ref_key_variants_yield_the_same_result() -> None:
    """A leg resolves through every spelling of its schedule references.

    ``schedules`` holding ``{"ref": <id>}`` objects is what live BFM cert
    responses publish; ``scheduleRef``/``scheduleRefs``/``ScheduleRef`` are the
    other spellings seen, including as a bare scalar.

    Regression: reading only one spelling dropped every itinerary, and the
    connector then answered SUCCESS with an empty results[] - a silent "no
    flights" on a shop that did return flights.
    """
    import copy

    for key, value in (
        ("schedules", [{"ref": 1}]),
        ("scheduleRef", [1]),
        ("scheduleRefs", [1]),
        ("ScheduleRef", [1]),
        ("scheduleRef", 1),
    ):
        payload = copy.deepcopy(SABRE_RESPONSE)
        for leg in payload["groupedItineraryResponse"]["legDescs"]:
            leg.pop("scheduleRefs", None)
            leg.pop("schedules", None)
            leg[key] = value

        home = SabreResponseTransformer().to_home_response(payload)

        assert len(home.results) == 1, key
        assert home.results[0].from_ == "BOM", key
        assert home.results[0].flightNumber == "805", key

#: The home result contract, field for field. Must stay identical to the Akasa
#: connector's, so the frontend receives one structure from every provider.
HOME_RESULT_FIELDS = frozenset(
    {
        "id",
        "provider",
        "from",
        "to",
        "departureDate",
        "arrivalDate",
        "departureTime",
        "arrivalTime",
        "airline",
        "flightNumber",
        "aircraft",
        "stops",
        "duration",
        "cabin",
        "bookingClass",
        "fareBasisCode",
        "seatsAvailable",
        "refundable",
        "baggage",
        "ADT",
        "CHD",
        "INF",
        "totalPrice",
        "journeyKey",
        "segmentKey",
        "fareAvailabilityKey",
        "isSumOfSector",
    }
)

SABRE_RESPONSE = {
    "groupedItineraryResponse": {
        "scheduleDescs": [
            {
                "id": 1,
                "carrier": "AI",
                "marketingCarrier": {"code": "AI"},
                "flightNumber": 805,
                "equipment": {"code": "320"},
                "elapsedTime": 155,
                "origin": "BOM",
                "destination": "DEL",
                "departureTime": "2026-11-06T06:00:00",
                "arrivalTime": "2026-11-06T08:35:00",
            }
        ],
        "legDescs": [{"id": 1, "ref": 1, "scheduleRefs": [1]}],
        "itineraryGroups": [
            {
                "itineraries": [
                    {
                        "id": 1,
                        "legs": [{"ref": 1}],
                        "pricingInformation": [
                            {
                                "fare": {
                                    "totalFare": {"amount": 8664.0, "currency": "INR"},
                                    "passengerInfoList": [
                                        {
                                            "passengerInfo": {
                                                "passengerType": "ADT",
                                                "passengerNumber": 1,
                                                "nonRefundable": False,
                                                "passengerTotalFare": {
                                                    "totalFare": 4332.0,
                                                    "baseFare": 3279.0,
                                                    "totalTaxAmount": 1053.0,
                                                },
                                                "fareComponents": [
                                                    {
                                                        "ref": 1,
                                                        "baggageAllowance": {
                                                            "weight": 25,
                                                            "unit": "KG",
                                                        },
                                                    }
                                                ],
                                            }
                                        },
                                        {
                                            "passengerInfo": {
                                                "passengerType": "ADT",
                                                "passengerNumber": 2,
                                                "nonRefundable": False,
                                                "passengerTotalFare": {
                                                    "totalFare": 4332.0,
                                                    "baseFare": 3279.0,
                                                    "totalTaxAmount": 1053.0,
                                                },
                                                "fareComponents": [{"ref": 1}],
                                            }
                                        },
                                    ],
                                }
                            }
                        ],
                    }
                ]
            }
        ],
        "fareComponentDescs": [
            {
                "id": 1,
                "fareBasisCode": "V4O7RBIX",
                "cabinCode": "Y",
                "seatsAvailable": 9,
                "segments": [{"segment": {"bookingCode": "V", "cabinCode": "Y"}}],
            }
        ],
    }
}


def test_sabre_response_transformer_maps_every_home_field() -> None:
    home = SabreResponseTransformer().to_home_response(SABRE_RESPONSE)

    assert isinstance(home, HomeSearchResponse)
    assert home.operation == "FLIGHT_SEARCH"
    assert home.currency == "INR"
    assert home.search_id is None  # stamped by Java ORBiS
    assert len(home.results) == 1

    result = home.results[0]
    assert result.provider == "SABRE"
    assert result.id == "1|0"
    assert result.from_ == "BOM"
    assert result.to == "DEL"
    assert result.departureDate == "2026-11-06"
    assert result.departureTime == "06:00:00"
    assert result.arrivalDate == "2026-11-06"
    assert result.arrivalTime == "08:35:00"
    assert result.airline == "AI"
    assert result.flightNumber == "805"
    assert result.aircraft == "320"
    assert result.stops == 0
    assert result.duration == "2h 35m"  # schedule elapsedTime
    assert result.cabin == "Y"
    assert result.bookingClass == "V"
    assert result.fareBasisCode == "V4O7RBIX"
    assert result.seatsAvailable == 9
    assert result.refundable is True  # !nonRefundable
    assert result.baggage.weight == 25
    assert result.baggage.unit == "KG"

    # Two ADT passengers are listed, one entry each.
    assert result.adt.count == 2
    assert result.adt.base_fare == 3279.0
    assert result.adt.tax == 1053.0
    assert result.adt.total == 4332.0
    assert result.chd is None
    assert result.inf is None
    assert result.totalPrice == 8664.0  # fare.totalFare.amount

    # Sabre has no journeyKey/segmentKey/fareAvailabilityKey of those names, so
    # its itinerary id, resolved schedule ids and fare-component ids are carried.
    assert result.journeyKey == "1"
    assert result.segmentKey == "1"
    assert result.fareAvailabilityKey == "1"

    # BFM publishes no sum-of-sector flag.
    assert result.isSumOfSector is None


def test_sabre_home_result_structure_is_identical_for_every_provider() -> None:
    import msgspec

    body = SabreResponseTransformer().to_home_response(SABRE_RESPONSE)
    decoded = msgspec.json.decode(msgspec.json.encode(body))

    assert set(decoded) == {"operation", "searchId", "currency", "results"}
    assert {frozenset(result) for result in decoded["results"]} == {HOME_RESULT_FIELDS}


def test_sabre_without_leg_descs_produces_no_results() -> None:
    """An unresolvable leg reference yields no result, never a half-mapped row."""
    home = SabreResponseTransformer().to_home_response(
        {
            "groupedItineraryResponse": {
                "schedules": [{"id": 7, "origin": "BOM", "destination": "BLR"}],
                "itineraryGroups": [
                    {
                        "itineraries": [
                            {
                                "id": 5,
                                "legs": [{"ref": 1}],
                                "pricingInformation": [{"fare": {"passengerInfoList": []}}],
                            }
                        ]
                    }
                ],
            }
        }
    )

    assert home.results == []


def test_sabre_maps_a_v4_style_body_through_legs_that_resolve() -> None:
    payload = {
        "groupedItineraryResponse": {
            "schedules": [
                {
                    "id": 7,
                    "carrier": "6E",
                    "flightNumber": 2045,
                    "equipmentType": "32N",
                    "elapsedTime": 95,
                    "origin": "BOM",
                    "destination": "BLR",
                    "departureTime": "2026-11-06T22:40:00",
                    "arrivalTime": "2026-11-06T23:20:00",
                    "arrivalDateAdjustment": 1,
                }
            ],
            "legDescs": [{"id": 1, "ref": 1, "scheduleRefs": [7]}],
            "itineraryGroups": [
                {
                    "itineraries": [
                        {
                            "id": 5,
                            "legs": [{"ref": 1}],
                            "pricingInformation": [
                                {
                                    "fare": {
                                        "passengerInfoList": [
                                            {
                                                "passengerInfo": {
                                                    "passengerType": "CHD",
                                                    "nonRefundable": True,
                                                    "passengerTotalFare": {
                                                        "totalFare": 2100.0,
                                                        "baseFare": 1600.0,
                                                        "totalTaxAmount": 500.0,
                                                    },
                                                    "fareComponents": [
                                                        {
                                                            "segments": [
                                                                {
                                                                    "segment": {
                                                                        "bookingCode": "S",
                                                                        "cabinCode": "M",
                                                                        "seatsAvailable": 4,
                                                                    }
                                                                }
                                                            ],
                                                            # pieces only: the home field is weight/unit
                                                            "baggageAllowance": {"pieces": 1},
                                                        }
                                                    ],
                                                }
                                            }
                                        ]
                                    }
                                }
                            ],
                        }
                    ]
                }
            ],
        }
    }

    home = SabreResponseTransformer().to_home_response(payload)
    assert len(home.results) == 1

    result = home.results[0]
    assert result.aircraft == "32N"  # v4 equipmentType
    assert result.cabin == "M"  # from the inline fare component
    assert result.bookingClass == "S"
    assert result.seatsAvailable == 4
    assert result.refundable is False
    assert result.duration == "1h 35m"  # elapsedTime, not the adjusted clock times
    assert result.arrivalDate == "2026-11-07"  # +1 day adjustment
    assert result.arrivalTime == "23:20:00"
    # A pieces-only allowance cannot be expressed as weight/unit.
    assert result.baggage is None
    assert home.currency is None  # no totalFare.currency in this body
    assert result.chd.count == 1
    assert result.chd.total == 2100.0


def test_sabre_empty_result_set_is_not_an_error() -> None:
    home = SabreResponseTransformer().to_home_response(
        {"groupedItineraryResponse": {"itineraryGroups": []}}
    )

    assert home.results == []


def test_sabre_rejects_a_payload_that_is_not_a_bfm_response() -> None:
    with pytest.raises(TransformationError):
        SabreResponseTransformer().to_home_response({"data": {"results": []}})


def test_unresolvable_fare_component_leaves_only_the_dependent_fields_null() -> None:
    """A fare whose component ref is missing still produces a priced result."""
    payload = {
        "groupedItineraryResponse": {
            "scheduleDescs": SABRE_RESPONSE["groupedItineraryResponse"]["scheduleDescs"],
            "legDescs": SABRE_RESPONSE["groupedItineraryResponse"]["legDescs"],
            "fareComponentDescs": [],
            "itineraryGroups": [
                {
                    "itineraries": [
                        {
                            "id": 9,
                            "legs": [{"ref": 1}],
                            "pricingInformation": [
                                {
                                    "fare": {
                                        "totalFare": {"amount": 5000.0, "currency": "INR"},
                                        "passengerInfoList": [
                                            {
                                                "passengerInfo": {
                                                    "passengerType": "ADT",
                                                    "passengerTotalFare": {
                                                        "totalFare": 5000.0,
                                                        "baseFare": 4000.0,
                                                        "totalTaxAmount": 1000.0,
                                                    },
                                                }
                                            }
                                        ],
                                    }
                                }
                            ],
                        }
                    ]
                }
            ],
        }
    }

    home = SabreResponseTransformer().to_home_response(payload)
    assert len(home.results) == 1

    result = home.results[0]
    assert result.totalPrice == 5000.0
    assert result.adt.total == 5000.0
    # Nothing resolvable for these, so they stay null rather than being guessed.
    assert result.cabin is None
    assert result.bookingClass is None
    assert result.fareBasisCode is None
    assert result.seatsAvailable is None
    assert result.baggage is None
    assert result.refundable is None
    assert result.fareAvailabilityKey is None


def test_live_bfm_legdescs_schedules_objects_resolve() -> None:
    """The shape a live BFM cert response returns, reproduced from the log.

    ``legDescs`` entries carry ``schedules: [{"ref": <id>}]`` objects with
    integer ids, and each itinerary leg references a leg desc by ``ref``. Both
    legs of the itinerary must resolve, in leg order, so the result spans the
    whole journey instead of being dropped.
    """
    payload = {
        "groupedItineraryResponse": {
            "scheduleDescs": [
                {
                    "id": 7,
                    "origin": "BOM",
                    "destination": "DEL",
                    "departureTime": "2026-11-06T06:00:00",
                    "arrivalTime": "2026-11-06T08:15:00",
                    "elapsedTime": 135,
                    "flightNumber": 805,
                    "marketingCarrier": {"code": "AI"},
                    "equipment": {"code": "32N"},
                },
                {
                    "id": 6,
                    "origin": "DEL",
                    "destination": "BOM",
                    "departureTime": "2026-11-11T18:00:00",
                    "arrivalTime": "2026-11-11T20:15:00",
                    "elapsedTime": 135,
                    "flightNumber": 806,
                    "marketingCarrier": {"code": "AI"},
                    "equipment": {"code": "32N"},
                },
            ],
            "legDescs": [
                {"id": 1, "elapsedTime": 135, "schedules": [{"ref": 7}]},
                {"id": 2, "elapsedTime": 135, "schedules": [{"ref": 6}]},
            ],
            "itineraryGroups": [
                {
                    "itineraries": [
                        {
                            "id": 1,
                            "legs": [{"ref": 1}, {"ref": 2}],
                            "pricingInformation": [
                                {
                                    "fare": {
                                        "totalFare": {
                                            "amount": 9000.0,
                                            "currency": "INR",
                                        },
                                        "passengerInfoList": [
                                            {
                                                "passengerInfo": {
                                                    "passengerType": "ADT",
                                                    "passengerTotalFare": {
                                                        "totalFare": 9000.0,
                                                        "baseFare": 7000.0,
                                                        "totalTaxAmount": 2000.0,
                                                    },
                                                }
                                            }
                                        ],
                                    }
                                }
                            ],
                        }
                    ]
                }
            ],
        }
    }

    home = SabreResponseTransformer().to_home_response(payload)

    assert len(home.results) == 1
    result = home.results[0]
    assert result.from_ == "BOM"
    assert result.to == "BOM"
    assert result.flightNumber == "805,806"
    assert result.segmentKey == "7,6"
    assert result.stops == 1
    assert result.departureDate == "2026-11-06"
    assert result.arrivalDate == "2026-11-11"
    assert result.journeyKey == "1"


def test_booking_code_reads_the_fare_component_when_the_segment_has_none() -> None:
    """BFM publishes ``bookingCode`` on the fare component, next to ``cabinCode``."""
    payload = {
        "groupedItineraryResponse": {
            "scheduleDescs": [
                {
                    "id": 1,
                    "origin": "BOM",
                    "destination": "DEL",
                    "departureTime": "2026-11-06T06:00:00",
                    "arrivalTime": "2026-11-06T08:15:00",
                    "elapsedTime": 135,
                    "flightNumber": 805,
                    "carrier": "AI",
                }
            ],
            "legDescs": [{"id": 1, "schedules": [{"ref": 1}]}],
            "fareComponentDescs": [
                {
                    "id": 1,
                    "cabinCode": "Y",
                    "bookingCode": "Y",
                    "fareBasisCode": "YLNXII",
                    "segments": [{"segment": {}}],
                }
            ],
            "itineraryGroups": [
                {
                    "itineraries": [
                        {
                            "id": 1,
                            "legs": [{"ref": 1}],
                            "pricingInformation": [
                                {
                                    "fare": {
                                        "totalFare": {"amount": 5000.0, "currency": "INR"},
                                        "fareComponents": [{"ref": 1}],
                                        "passengerInfoList": [
                                            {
                                                "passengerInfo": {
                                                    "passengerType": "ADT",
                                                    "passengerTotalFare": {
                                                        "totalFare": 5000.0,
                                                        "totalTaxAmount": 1000.0,
                                                    },
                                                }
                                            }
                                        ],
                                    }
                                }
                            ],
                        }
                    ]
                }
            ],
        }
    }

    home = SabreResponseTransformer().to_home_response(payload)

    result = home.results[0]
    assert result.cabin == "Y"
    assert result.bookingClass == "Y"
    assert result.fareBasisCode == "YLNXII"


def test_unexpected_schedule_shape_is_reported_not_silent(caplog) -> None:
    """A schedule descriptor without the flight keys warns instead of going quiet.

    The mapper must not guess ``from``/``airline``/``flightNumber`` from another
    node, so the fields stay null - but the response cannot look like a normal
    empty one, or a mapping gap reads as "no flights".
    """
    import logging

    payload = {
        "groupedItineraryResponse": {
            "scheduleDescs": [{"id": 1, "elapsedTime": 135}],
            "legDescs": [{"id": 1, "schedules": [{"ref": 1}]}],
            "itineraryGroups": [
                {
                    "itineraries": [
                        {
                            "id": 1,
                            "legs": [{"ref": 1}],
                            "pricingInformation": [
                                {
                                    "fare": {
                                        "totalFare": {"amount": 5000.0, "currency": "INR"},
                                        "passengerInfoList": [
                                            {
                                                "passengerInfo": {
                                                    "passengerType": "ADT",
                                                    "passengerTotalFare": {
                                                        "totalFare": 5000.0,
                                                        "totalTaxAmount": 1000.0,
                                                    },
                                                }
                                            }
                                        ],
                                    }
                                }
                            ],
                        }
                    ]
                }
            ],
        }
    }

    with caplog.at_level(logging.WARNING):
        home = SabreResponseTransformer().to_home_response(payload)

    assert len(home.results) == 1
    assert home.results[0].from_ is None      # never guessed
    assert home.results[0].airline is None

    warnings = [record.getMessage() for record in caplog.records]
    assert any("schedule keys" in message for message in warnings), warnings


def _compact_payload(departure_clock: str = "00:15:00+05:30",
                     arrival_clock: str = "03:10:00+05:30",
                     elapsed: int = 175) -> dict:
    """A BFM response in the compact shape a live cert response returns.

    Reproduced from the descriptor a real search logged: times are local with an
    offset and carry no date, the airports live in ``departure``/``arrival``
    nodes, the airline in a ``carrier`` node, and the dates are stated once per
    leg in the itinerary group's description.
    """
    return {
        "groupedItineraryResponse": {
            "scheduleDescs": [
                {
                    "id": 7,
                    "frequency": "SM*W*F*",
                    "stopCount": 0,
                    "eTicketable": True,
                    "totalMilesFlown": 1058,
                    "elapsedTime": elapsed,
                    "departure": {
                        "airport": "BLR",
                        "city": "BLR",
                        "country": "IN",
                        "time": departure_clock,
                        "terminal": "2",
                    },
                    "arrival": {
                        "airport": "DEL",
                        "city": "DEL",
                        "country": "IN",
                        "time": arrival_clock,
                        "terminal": "3",
                    },
                    "carrier": {
                        "marketing": "AI",
                        "marketingFlightNumber": 2758,
                        "operating": "AI",
                        "operatingFlightNumber": 2758,
                        "equipment": {"code": "32N", "typeForFirstLeg": "N"},
                    },
                },
                {
                    "id": 2,
                    "elapsedTime": elapsed,
                    "departure": {
                        "airport": "DEL",
                        "time": departure_clock,
                        "terminal": "3",
                    },
                    "arrival": {"airport": "BLR", "time": arrival_clock, "terminal": "2"},
                    "carrier": {
                        "marketing": "AI",
                        "marketingFlightNumber": 2759,
                        "equipment": {"code": "32N"},
                    },
                },
            ],
            "legDescs": [
                {"id": 1, "elapsedTime": elapsed, "schedules": [{"ref": 7}]},
                {"id": 2, "elapsedTime": elapsed, "schedules": [{"ref": 2}]},
            ],
            "itineraryGroups": [
                {
                    "groupDescription": {
                        "legDescriptions": [
                            {
                                "departureDate": "2026-11-06",
                                "departureLocation": "BLR",
                                "arrivalLocation": "DEL",
                            },
                            {
                                "departureDate": "2026-11-11",
                                "departureLocation": "DEL",
                                "arrivalLocation": "BLR",
                            },
                        ]
                    },
                    "itineraries": [
                        {
                            "id": 1,
                            "legs": [{"ref": 1}, {"ref": 2}],
                            "pricingInformation": [
                                {
                                    "fare": {
                                        "totalFare": {"amount": 41268.0, "currency": "INR"},
                                        "passengerInfoList": [
                                            {
                                                "passengerInfo": {
                                                    "passengerType": "ADT",
                                                    "nonRefundable": True,
                                                    "passengerTotalFare": {
                                                        "totalFare": 20634.0,
                                                        "baseFareAmount": 16678.0,
                                                        "totalTaxAmount": 3956.0,
                                                        "baseFareCurrency": "INR",
                                                        "currency": "INR",
                                                    },
                                                }
                                            },
                                            {
                                                "passengerInfo": {
                                                    "passengerType": "ADT",
                                                    "nonRefundable": True,
                                                    "passengerTotalFare": {
                                                        "totalFare": 20634.0,
                                                        "baseFareAmount": 16678.0,
                                                        "totalTaxAmount": 3956.0,
                                                    },
                                                }
                                            },
                                        ],
                                    }
                                }
                            ],
                        }
                    ],
                }
            ],
        }
    }


def test_compact_schedule_shape_maps_every_flight_field(caplog) -> None:
    """Every schedule field resolves from the compact shape - none stays null."""
    import logging

    with caplog.at_level(logging.WARNING):
        home = SabreResponseTransformer().to_home_response(_compact_payload())

    assert len(home.results) == 1
    result = home.results[0]

    assert result.from_ == "BLR"
    assert result.to == "BLR"
    assert result.departureDate == "2026-11-06"
    assert result.departureTime == "00:15:00"      # offset dropped
    assert result.arrivalDate == "2026-11-11"      # date of the return leg
    assert result.arrivalTime == "03:10:00"
    assert result.airline == "AI"
    assert result.flightNumber == "2758,2759"
    assert result.aircraft == "32N"

    # The reference keys a later pricing/booking call needs are untouched.
    assert result.journeyKey == "1"
    assert result.segmentKey == "7,2"

    # baseFareAmount is what the provider publishes - not "baseFare".
    assert result.adt is not None
    assert result.adt.count == 2
    assert result.adt.base_fare == 16678.0
    assert result.adt.tax == 3956.0
    assert result.adt.total == 20634.0

    # Nothing was guessed, so the diagnostic does not fire.
    assert not [
        record for record in caplog.records if "schedule keys" in record.getMessage()
    ]


def test_compact_night_flight_arrives_on_the_next_day() -> None:
    """An overnight arrival lands on the following date, not the departure date.

    The journey's arrival comes from the last leg, so a red-eye return that
    departs on the 11th at 23:50 and flies 175 minutes arrives on the 12th - not
    on the 11th, which is the date the leg description states.
    """
    home = SabreResponseTransformer().to_home_response(
        _compact_payload(
            departure_clock="23:50:00+05:30",
            arrival_clock="02:45:00+05:30",
        )
    )

    result = home.results[0]
    assert result.departureDate == "2026-11-06"
    assert result.arrivalDate == "2026-11-12"
    assert result.arrivalTime == "02:45:00"
