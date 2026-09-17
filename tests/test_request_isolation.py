"""Request-isolation tests.

The transformer must be completely request-driven: every
POST /api/flights/search request is independently decoded and
transformed from the values received in that request. No Home Payload
is stored globally and no previous request's values may leak into a
later request's transformed payload.
"""

from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.config.settings import Settings
from app.main import create_app
from app.services.flight_search import FlightSearchService


def make_settings(**overrides) -> Settings:
    defaults = {
        "search_number_of_trips": 20,
        "search_max_connections": 4,
    }
    defaults.update(overrides)
    return Settings(**defaults)


def make_client() -> TestClient:
    """Build the transformation-only app (no outbound calls exist)."""
    settings = make_settings()
    app = create_app(settings)
    app.state.search_service = FlightSearchService(settings)
    return TestClient(app)


PAYLOAD_A = {
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
    "refundable": None,
    "filters": {"stops": {"min": 0, "max": 1}, "airlines": []},
}

PAYLOAD_B = {
    "operation": "FLIGHT_SEARCH",
    "from": "BOM",
    "to": "BLR",
    "dateFrom": "2027-02-20",
    "dateTo": "2027-02-25",
    "tripType": "ONE_WAY",
    "ADT": 3,
    "CHD": 0,
    "INF": 2,
    "currency": "USD",
    "cabin": "BUSINESS",
    "refundable": True,
    "filters": {"stops": {"min": 0, "max": 0}, "airlines": ["AI"]},
}


def _rq(response_json: dict[str, Any]) -> dict[str, Any]:
    return response_json["OTA_AirLowFareSearchRQ"]


def test_transform_returns_raw_sabre_body():
    client = make_client()
    response = client.post("/api/flights/search", json=PAYLOAD_A)

    assert response.status_code == 200
    body = response.json()

    # Raw Sabre body: no wrapper envelope.
    assert set(body.keys()) == {"OTA_AirLowFareSearchRQ"}
    assert body["OTA_AirLowFareSearchRQ"]["Version"] == "5"


def test_second_request_does_not_reuse_first_request_values():
    client = make_client()

    first = _rq(client.post("/api/flights/search", json=PAYLOAD_A).json())
    second = _rq(client.post("/api/flights/search", json=PAYLOAD_B).json())

    # Outbound leg reflects each request's own origin/destination/date.
    assert first["OriginDestinationInformation"][0]["OriginLocation"][
        "LocationCode"
    ] == "MAA"
    assert first["OriginDestinationInformation"][0]["DestinationLocation"][
        "LocationCode"
    ] == "DEL"
    assert first["OriginDestinationInformation"][0][
        "DepartureDateTime"
    ] == "2026-11-04T00:00:00"

    assert second["OriginDestinationInformation"][0]["OriginLocation"][
        "LocationCode"
    ] == "BOM"
    assert second["OriginDestinationInformation"][0]["DestinationLocation"][
        "LocationCode"
    ] == "BLR"
    assert second["OriginDestinationInformation"][0][
        "DepartureDateTime"
    ] == "2027-02-20T00:00:00"


def test_trip_type_taken_from_current_request_only():
    client = make_client()

    first = _rq(client.post("/api/flights/search", json=PAYLOAD_A).json())
    second = _rq(client.post("/api/flights/search", json=PAYLOAD_B).json())

    assert len(first["OriginDestinationInformation"]) == 2  # has return leg
    assert len(second["OriginDestinationInformation"]) == 1  # no return leg

    # The return leg of request A must carry A's return date.
    assert first["OriginDestinationInformation"][1][
        "DepartureDateTime"
    ] == "2026-11-11T00:00:00"


def test_passenger_counts_taken_from_current_request_only():
    client = make_client()

    first = _rq(client.post("/api/flights/search", json=PAYLOAD_A).json())
    second = _rq(client.post("/api/flights/search", json=PAYLOAD_B).json())

    def ptq(rq: dict[str, Any]) -> dict[str, int]:
        quantities = rq["TravelerInfoSummary"]["AirTravelerAvail"][0][
            "PassengerTypeQuantity"
        ]
        return {p["Code"]: p["Quantity"] for p in quantities}

    assert ptq(first) == {"ADT": 1, "CHD": 1}  # A: no INF
    assert ptq(second) == {"ADT": 3, "INF": 2}  # B: no CHD


def test_interleaved_requests_stay_isolated():
    """Alternating payloads must not bleed values into each other."""
    client = make_client()

    responses = [
        client.post("/api/flights/search", json=PAYLOAD_B).json(),
        client.post("/api/flights/search", json=PAYLOAD_A).json(),
        client.post("/api/flights/search", json=PAYLOAD_B).json(),
    ]

    for response_json, payload in zip(
        responses, (PAYLOAD_B, PAYLOAD_A, PAYLOAD_B)
    ):
        leg = _rq(response_json)["OriginDestinationInformation"][0]
        assert leg["OriginLocation"]["LocationCode"] == payload["from"]
        assert leg["DestinationLocation"]["LocationCode"] == payload["to"]
        assert leg["DepartureDateTime"] == (
            f"{payload['dateFrom']}T00:00:00"
        )


def test_service_holds_no_payload_state_between_transforms():
    """Direct service-level check: no attributes retain a payload."""
    client = make_client()
    service = client.app.state.search_service

    client.post("/api/flights/search", json=PAYLOAD_A)
    for value in vars(service).values():
        assert "MAA" not in str(value)

    client.post("/api/flights/search", json=PAYLOAD_B)
    for value in vars(service).values():
        assert "BOM" not in str(value)


def test_no_outbound_http_module_in_service_flow():
    """The service must not perform provider calls: transformation only."""
    import app.services.flight_search as fs_module

    source_attrs = vars(fs_module)
    assert "httpx" not in source_attrs
    assert not hasattr(fs_module, "SabreProviderClient")
    assert not hasattr(fs_module, "SabreTokenProvider")
