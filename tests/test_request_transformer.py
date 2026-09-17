"""Tests for the request transformer (Home Payload -> Sabre BFM).

Uses actual input values from the defined Home Payload only.
"""

import pytest

from app.config.settings import Settings
from app.exceptions.integration import InvalidHomePayloadError
from app.models.home_payload import HomePayload
from app.services.flight_search import FlightSearchService
from app.transformer.request import transform_request


def make_settings(**overrides) -> Settings:
    defaults = {
        "search_number_of_trips": 20,
        "search_max_connections": 4,
        "search_enable_atpco": True,
        "search_enable_ndc": True,
    }
    defaults.update(overrides)
    return Settings(**defaults)


def make_payload(**overrides) -> dict:
    base = {
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
    base.update(overrides)
    return base


def build_request(payload_overrides: dict | None = None, **settings_overrides):
    import msgspec

    payload = msgspec.convert(
        make_payload(**(payload_overrides or {})), HomePayload, strict=False
    )
    return transform_request(payload, make_settings(**settings_overrides))


def test_round_trip_produces_two_legs():
    body = build_request()
    rq = body["OTA_AirLowFareSearchRQ"]
    legs = rq["OriginDestinationInformation"]

    assert len(legs) == 2
    assert legs[0]["OriginLocation"]["LocationCode"] == "MAA"
    assert legs[0]["DestinationLocation"]["LocationCode"] == "DEL"
    assert legs[0]["DepartureDateTime"] == "2026-11-04T00:00:00"
    assert legs[1]["OriginLocation"]["LocationCode"] == "DEL"
    assert legs[1]["DestinationLocation"]["LocationCode"] == "MAA"
    assert legs[1]["DepartureDateTime"] == "2026-11-11T00:00:00"


def test_one_way_produces_single_leg():
    body = build_request({"tripType": "ONE_WAY"})
    rq = body["OTA_AirLowFareSearchRQ"]
    legs = rq["OriginDestinationInformation"]

    assert len(legs) == 1
    assert legs[0]["OriginLocation"]["LocationCode"] == "MAA"
    assert legs[0]["DestinationLocation"]["LocationCode"] == "DEL"


def test_passenger_quantities_and_zero_omitted():
    body = build_request({"ADT": 2, "CHD": 1, "INF": 0})
    ptq = body["OTA_AirLowFareSearchRQ"]["TravelerInfoSummary"][
        "AirTravelerAvail"
    ][0]["PassengerTypeQuantity"]

    by_code = {p["Code"]: p["Quantity"] for p in ptq}
    assert by_code == {"ADT": 2, "CHD": 1}
    assert "INF" not in by_code


def test_no_pcc_or_credentials_in_transformed_request():
    """Python must not include Sabre credentials; Java injects them."""
    import json

    body = build_request()
    serialized = json.dumps(body).lower()

    assert "pseudocitycode" not in serialized
    assert "pcc" not in serialized
    assert "token" not in serialized
    assert "authorization" not in serialized
    assert "password" not in serialized


def test_requestor_id_shape_confirmed_by_collection():
    body = build_request()
    source = body["OTA_AirLowFareSearchRQ"]["POS"]["Source"][0]

    assert source["RequestorID"] == {
        "Type": "1",
        "ID": "1",
        "CompanyName": {"Code": "TN"},
    }
    assert "PseudoCityCode" not in source


def test_data_sources_reflect_settings():
    body = build_request(search_enable_atpco=True, search_enable_ndc=False)
    sources = body["OTA_AirLowFareSearchRQ"]["TravelPreferences"][
        "TPA_Extensions"
    ]["DataSources"]

    assert sources == {"ATPCO": "Enable", "NDC": "Disable"}


def test_atpco_and_ndc_enabled_by_default():
    body = build_request()
    sources = body["OTA_AirLowFareSearchRQ"]["TravelPreferences"][
        "TPA_Extensions"
    ]["DataSources"]

    assert sources == {"ATPCO": "Enable", "NDC": "Enable"}


def test_station_codes_uppercased():
    body = build_request({"from": "mAA", "to": "del"})
    rq = body["OTA_AirLowFareSearchRQ"]

    assert rq["OriginDestinationInformation"][0]["OriginLocation"][
        "LocationCode"
    ] == "MAA"
    assert rq["OriginDestinationInformation"][0]["DestinationLocation"][
        "LocationCode"
    ] == "DEL"


def test_invalid_trip_type_rejected():
    from app.codec import validate_home_payload

    with pytest.raises(InvalidHomePayloadError):
        validate_home_payload(make_payload(tripType="ONEWAY"))


def test_invalid_date_rejected():
    from app.codec import validate_home_payload

    with pytest.raises(InvalidHomePayloadError):
        validate_home_payload(make_payload(dateFrom="04-11-2026"))


def test_unsupported_operation_raises():
    import msgspec

    payload = msgspec.convert(
        make_payload(operation="FLIGHT_BOOK"), HomePayload, strict=False
    )
    with pytest.raises(Exception) as excinfo:
        payload.validate_operation()
    assert "FLIGHT_SEARCH" in str(excinfo.value)


def test_malformed_json_raises_controlled_error():
    from app.codec import decode_home_payload

    with pytest.raises(InvalidHomePayloadError):
        decode_home_payload("{not json")


def test_invalid_payload_raises_controlled_error():
    settings = make_settings()
    service = FlightSearchService(settings)
    raw = make_payload()
    raw["dateFrom"] = "04-11-2026"

    with pytest.raises(InvalidHomePayloadError):
        service.transform(raw)


def test_unmapped_fields_validated_but_absent_from_sabre_request():
    """cabin/currency/refundable/filters have no confirmed mapping:
    they are validated but must not leak into the Sabre request."""
    import json

    body = build_request(
        {
            "cabin": "BUSINESS",
            "currency": "USD",
            "refundable": True,
            "filters": {"stops": {"min": 0, "max": 0}, "airlines": ["AI"]},
        }
    )
    serialized = json.dumps(body).lower()

    assert "cabin" not in serialized
    assert "currency" not in serialized
    assert "refundable" not in serialized
    assert "filters" not in serialized
    assert "airlines" not in serialized


def test_wrapped_connector_search_request_accepted():
    """Java sends ConnectorSearchRequest where the Home Payload is in 'search'."""
    from app.codec import validate_home_payload

    wrapped = {
        "providerCode": "SABRE",
        "apiType": "sabre_ndc",
        "search": make_payload(),
    }
    payload = validate_home_payload(wrapped)
    assert payload.from_location == "MAA"
    assert payload.to_location == "DEL"

