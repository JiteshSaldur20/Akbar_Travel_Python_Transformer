"""Tests for POST /api/flights/search - Java ORBiS -> Python Sabre connector."""

from __future__ import annotations

import httpx
import msgspec
import pytest
from fastapi.testclient import TestClient

from app.api.search import set_sabre_client
from app.connectors.java_auth_client import JavaAuthClient
from app.connectors.sabre_client import SabreClient
from app.core.config import Settings
from app.main import app

client = TestClient(app)

JAVA_COMMON_REQUEST = {
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

MOCK_SETTINGS = Settings(
    java_internal_url="http://localhost:8080",
    sabre_base_url="https://mock.sabre.com",
    sabre_search_path="v5/shop/flights",
)


@pytest.fixture(autouse=True)
def reset_client():
    yield
    set_sabre_client(None)


def _sabre_success_body() -> bytes:
    return msgspec.json.encode(
        {"OTA_AirLowFareSearchRS": {"Version": "5.0.0", "PricedItineraries": []}}
    )


def _make_client(transport: httpx.BaseTransport) -> None:
    java_auth_client = JavaAuthClient(settings=MOCK_SETTINGS, transport=transport)
    set_sabre_client(
        SabreClient(
            settings=MOCK_SETTINGS,
            java_auth_client=java_auth_client,
            transport=transport,
        )
    )


def test_search_full_flow_success_with_java_auth() -> None:
    captured: list[httpx.Request] = []

    def mock_handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        if "/internal/providers/SABRE/auth" in str(request.url):
            return httpx.Response(
                200,
                content=msgspec.json.encode(
                    {
                        "providerCode": "SABRE",
                        "accessToken": "mock-java-provided-sabre-token-99999",
                        "tokenType": "Bearer",
                        "expiresAt": "2026-10-01T12:00:00Z",
                        "pcc": "86AD",
                    }
                ),
                headers={"Content-Type": "application/json"},
            )
        if "v5/shop/flights" in str(request.url):
            return httpx.Response(
                200,
                content=_sabre_success_body(),
                headers={"Content-Type": "application/json"},
            )
        return httpx.Response(404)

    _make_client(httpx.MockTransport(mock_handler))

    resp = client.post("/api/flights/search", json=JAVA_COMMON_REQUEST)
    assert resp.status_code == 200

    resp_data = msgspec.json.decode(resp.content)
    assert "OTA_AirLowFareSearchRS" in resp_data

    assert len(captured) == 2

    java_auth_req = captured[0]
    assert java_auth_req.method == "GET"
    assert "/internal/providers/SABRE/auth" in str(java_auth_req.url)
    assert "sabre_ndc" in str(java_auth_req.url)

    sabre_req = captured[1]
    assert sabre_req.method == "POST"
    assert (
        sabre_req.headers["authorization"]
        == "Bearer mock-java-provided-sabre-token-99999"
    )
    assert "v5/shop/flights" in str(sabre_req.url)

    sabre_payload = msgspec.json.decode(sabre_req.content)
    rq = sabre_payload["OTA_AirLowFareSearchRQ"]
    assert rq["Version"] == "5"

    # The PCC returned by Java must reach POS.Source[0], otherwise Sabre
    # answers 400 "Unable to determine PseudoCityCode".
    source = rq["POS"]["Source"][0]
    assert source["PseudoCityCode"] == "86AD"
    assert source["RequestorID"] == {
        "Type": "1",
        "ID": "1",
        "CompanyName": {"Code": "TN"},
    }

    legs = rq["OriginDestinationInformation"]
    assert len(legs) == 2
    assert legs[0]["OriginLocation"]["LocationCode"] == "MAA"
    assert legs[0]["DestinationLocation"]["LocationCode"] == "DEL"
    assert legs[0]["DepartureDateTime"] == "2026-11-04T00:00:00"
    assert legs[1]["OriginLocation"]["LocationCode"] == "DEL"
    assert legs[1]["DestinationLocation"]["LocationCode"] == "MAA"
    assert legs[1]["DepartureDateTime"] == "2026-11-11T00:00:00"

    ptq = rq["TravelerInfoSummary"]["AirTravelerAvail"][0]["PassengerTypeQuantity"]
    assert ptq == [{"Code": "ADT", "Quantity": 1}, {"Code": "CHD", "Quantity": 1}]


def _capture_bfm_request(payload: dict) -> dict:
    """Post `payload` and return the OTA_AirLowFareSearchRQ the connector sent to Sabre."""
    captured: list[httpx.Request] = []

    def mock_handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        if "/internal/providers/SABRE/auth" in str(request.url):
            return httpx.Response(
                200,
                content=msgspec.json.encode(
                    {
                        "providerCode": "SABRE",
                        "accessToken": "token-1",
                        "tokenType": "Bearer",
                        "pcc": "86AD",
                    }
                ),
                headers={"Content-Type": "application/json"},
            )
        return httpx.Response(
            200,
            content=_sabre_success_body(),
            headers={"Content-Type": "application/json"},
        )

    _make_client(httpx.MockTransport(mock_handler))

    resp = client.post("/api/flights/search", json=payload)
    assert resp.status_code == 200

    sabre_req = captured[-1]
    assert "v5/shop/flights" in str(sabre_req.url)
    return msgspec.json.decode(sabre_req.content)["OTA_AirLowFareSearchRQ"]


@pytest.mark.parametrize(
    "trip_type, expected_legs",
    [
        ("ONE_WAY", 1),
        ("one-way", 1),
        ("ROUND_TRIP", 2),
        (None, 2),
    ],
)
def test_trip_type_decides_whether_a_return_leg_is_shopped(
    trip_type: str | None, expected_legs: int
) -> None:
    """dateTo alone must not turn a ONE_WAY search into a round trip.

    A second OriginDestinationInformation leg makes Sabre shop and price a
    whole extra set of itineraries, which roughly doubles the BFM response
    time. Without a tripType the previous behaviour is kept, so existing
    callers are unaffected.
    """
    payload = {**JAVA_COMMON_REQUEST, "dateTo": "2026-11-11"}
    if trip_type is None:
        payload.pop("tripType")
    else:
        payload["tripType"] = trip_type

    rq = _capture_bfm_request(payload)

    legs = rq["OriginDestinationInformation"]
    assert len(legs) == expected_legs

    assert legs[0]["OriginLocation"]["LocationCode"] == "MAA"
    assert legs[0]["DestinationLocation"]["LocationCode"] == "DEL"
    assert legs[0]["DepartureDateTime"] == "2026-11-04T00:00:00"

    if expected_legs == 2:
        assert legs[1]["OriginLocation"]["LocationCode"] == "DEL"
        assert legs[1]["DestinationLocation"]["LocationCode"] == "MAA"
        assert legs[1]["DepartureDateTime"] == "2026-11-11T00:00:00"


def test_search_java_auth_failure_returns_error() -> None:
    def mock_handler(request: httpx.Request) -> httpx.Response:
        if "/internal/providers/SABRE/auth" in str(request.url):
            return httpx.Response(
                500,
                content=b'{"error": "Provider authentication failed in Java"}',
                headers={"Content-Type": "application/json"},
            )
        return httpx.Response(404)

    _make_client(httpx.MockTransport(mock_handler))

    resp = client.post("/api/flights/search", json=JAVA_COMMON_REQUEST)
    assert resp.status_code == 502
    body = msgspec.json.decode(resp.content)
    assert body["error"] == "provider_api_error"


def test_search_without_pcc_fails_before_calling_sabre() -> None:
    """Java returned a token but no PCC: Sabre would reject the shop request
    with "Unable to determine PseudoCityCode", so the connector must stop and
    report the configuration problem instead."""
    sabre_called = False

    def mock_handler(request: httpx.Request) -> httpx.Response:
        nonlocal sabre_called
        if "/internal/providers/SABRE/auth" in str(request.url):
            return httpx.Response(
                200,
                content=msgspec.json.encode(
                    {
                        "providerCode": "SABRE",
                        "accessToken": "token-without-pcc",
                        "tokenType": "Bearer",
                    }
                ),
            )
        sabre_called = True
        return httpx.Response(400, content=b"{}")

    _make_client(httpx.MockTransport(mock_handler))

    resp = client.post("/api/flights/search", json=JAVA_COMMON_REQUEST)
    assert resp.status_code == 422
    body = msgspec.json.decode(resp.content)
    assert body["error"] == "transformation_failed"
    assert "PseudoCityCode" in body["message"]
    assert sabre_called is False


def test_search_provider_error_returned_faithfully() -> None:
    def mock_handler(request: httpx.Request) -> httpx.Response:
        if "/internal/providers/SABRE/auth" in str(request.url):
            return httpx.Response(
                200,
                content=msgspec.json.encode(
                    {
                        "providerCode": "SABRE",
                        "accessToken": "token-1",
                        "tokenType": "Bearer",
                        "pcc": "86AD",
                    }
                ),
            )
        return httpx.Response(
            400,
            content=msgspec.json.encode(
                {
                    "OTA_AirLowFareSearchRS": {
                        "Errors": [{"Code": "RULE_VIOLATION", "Message": "Bad dates"}]
                    }
                }
            ),
            headers={"Content-Type": "application/json"},
        )

    _make_client(httpx.MockTransport(mock_handler))

    resp = client.post("/api/flights/search", json=JAVA_COMMON_REQUEST)
    assert resp.status_code == 400
    body = msgspec.json.decode(resp.content)
    assert "OTA_AirLowFareSearchRS" in body
    assert body["OTA_AirLowFareSearchRS"]["Errors"][0]["Code"] == "RULE_VIOLATION"


def test_search_missing_required_fields_returns_422() -> None:
    resp = client.post(
        "/api/flights/search",
        json={"operation": "FLIGHT_SEARCH", "from": "", "to": "DEL"},
    )
    assert resp.status_code == 422
    body = msgspec.json.decode(resp.content)
    assert body["error"] == "transformation_failed"


def test_search_empty_body_returns_422() -> None:
    resp = client.post("/api/flights/search", content=b"")
    assert resp.status_code == 422