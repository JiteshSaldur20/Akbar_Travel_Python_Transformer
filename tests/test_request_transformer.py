"""Tests for the Sabre request transformer (home request -> Sabre BFM).

Uses the canonical home request model that Java ORBiS forwards to every
provider microservice.
"""

from __future__ import annotations

import json

import pytest

from app.core.config import Settings
from app.schemas.home_payload import HomeSearchRequest, Passenger
from app.transformers.sabre_request_transformer import SabreRequestTransformer


def make_home(**overrides) -> HomeSearchRequest:
    defaults = {
        "origin": "MAA",
        "destination": "DEL",
        "depart_date": "2026-11-04",
        "return_date": "2026-11-11",
        "currency": "INR",
        "cabin": "ECONOMY",
        "passengers": [
            Passenger(ptc="ADT", count=1),
            Passenger(ptc="CHD", count=1),
        ],
    }
    defaults.update(overrides)
    return HomeSearchRequest(**defaults)


def make_settings(**overrides) -> Settings:
    defaults = {
        "search_number_of_trips": 20,
        "search_prefer_ndc_on_tie": False,
        "search_enable_atpco": True,
        "search_enable_ndc": True,
        "search_max_upsells": 0,
        "search_multiple_branded_fares": False,
    }
    defaults.update(overrides)
    return Settings(**defaults)


def build_request(
    monkeypatch: pytest.MonkeyPatch,
    home_overrides: dict | None = None,
    settings_overrides: dict | None = None,
) -> dict:
    """Build a Sabre BFM body from a home request + settings override."""
    settings = make_settings(**(settings_overrides or {}))
    monkeypatch.setattr(
        "app.transformers.sabre_request_transformer.get_settings",
        lambda: settings,
    )
    return SabreRequestTransformer().to_provider_request(
        make_home(**(home_overrides or {}))
    )


def test_round_trip_produces_two_legs(monkeypatch: pytest.MonkeyPatch) -> None:
    body = build_request(monkeypatch)
    rq = body["OTA_AirLowFareSearchRQ"]
    legs = rq["OriginDestinationInformation"]

    assert len(legs) == 2
    assert legs[0]["OriginLocation"]["LocationCode"] == "MAA"
    assert legs[0]["DestinationLocation"]["LocationCode"] == "DEL"
    assert legs[0]["DepartureDateTime"] == "2026-11-04T00:00:00"
    assert legs[1]["OriginLocation"]["LocationCode"] == "DEL"
    assert legs[1]["DestinationLocation"]["LocationCode"] == "MAA"
    assert legs[1]["DepartureDateTime"] == "2026-11-11T00:00:00"


def test_one_way_produces_single_leg(monkeypatch: pytest.MonkeyPatch) -> None:
    body = build_request(monkeypatch, home_overrides={"return_date": None})
    rq = body["OTA_AirLowFareSearchRQ"]
    legs = rq["OriginDestinationInformation"]

    assert len(legs) == 1
    assert legs[0]["OriginLocation"]["LocationCode"] == "MAA"
    assert legs[0]["DestinationLocation"]["LocationCode"] == "DEL"


def test_passenger_quantities_and_zero_omitted(monkeypatch: pytest.MonkeyPatch) -> None:
    body = build_request(
        monkeypatch,
        home_overrides={
            "passengers": [
                Passenger(ptc="ADT", count=2),
                Passenger(ptc="CHD", count=1),
                Passenger(ptc="INF", count=0),
            ],
        },
    )
    ptq = body["OTA_AirLowFareSearchRQ"]["TravelerInfoSummary"][
        "AirTravelerAvail"
    ][0]["PassengerTypeQuantity"]

    by_code = {p["Code"]: p["Quantity"] for p in ptq}
    assert by_code == {"ADT": 2, "CHD": 1}
    assert "INF" not in by_code


def test_defaults_to_single_adt_when_no_passengers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = build_request(monkeypatch, home_overrides={"passengers": []})
    ptq = body["OTA_AirLowFareSearchRQ"]["TravelerInfoSummary"][
        "AirTravelerAvail"
    ][0]["PassengerTypeQuantity"]

    assert ptq == [{"Code": "ADT", "Quantity": 1}]


def test_no_pcc_or_credentials_in_transformed_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The BFM body must not carry credentials/tokens; auth is a header."""
    body = build_request(monkeypatch)
    serialized = json.dumps(body).lower()

    assert "pseudocitycode" not in serialized
    assert "pcc" not in serialized
    assert "token" not in serialized
    assert "authorization" not in serialized
    assert "password" not in serialized
    assert "username" not in serialized


def test_requestor_id_shape_confirmed_by_collection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = build_request(monkeypatch)
    source = body["OTA_AirLowFareSearchRQ"]["POS"]["Source"][0]

    assert source["RequestorID"] == {
        "Type": "1",
        "ID": "1",
        "CompanyName": {"Code": "TN"},
    }
    assert "PseudoCityCode" not in source


def test_data_sources_reflect_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    body = build_request(
        monkeypatch,
        settings_overrides={"search_enable_atpco": True, "search_enable_ndc": False},
    )
    sources = body["OTA_AirLowFareSearchRQ"]["TravelPreferences"][
        "TPA_Extensions"
    ]["DataSources"]

    assert sources == {"ATPCO": "Enable", "NDC": "Disable"}


def test_atpco_and_ndc_enabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    body = build_request(monkeypatch)
    sources = body["OTA_AirLowFareSearchRQ"]["TravelPreferences"][
        "TPA_Extensions"
    ]["DataSources"]

    assert sources == {"ATPCO": "Enable", "NDC": "Enable"}


def test_ndc_indicators_reflect_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    body = build_request(
        monkeypatch,
        settings_overrides={
            "search_multiple_branded_fares": True,
            "search_max_upsells": 4,
            "search_prefer_ndc_on_tie": True,
        },
    )
    indicators = body["OTA_AirLowFareSearchRQ"]["TravelPreferences"][
        "TPA_Extensions"
    ]["NDCIndicators"]

    assert indicators == {
        "MultipleBrandedFares": {"Value": True},
        "MaxNumberOfUpsells": {"Value": 4},
    }


def test_station_codes_uppercased(monkeypatch: pytest.MonkeyPatch) -> None:
    body = build_request(
        monkeypatch, home_overrides={"origin": "mAA", "destination": "del"}
    )
    rq = body["OTA_AirLowFareSearchRQ"]

    assert rq["OriginDestinationInformation"][0]["OriginLocation"][
        "LocationCode"
    ] == "MAA"
    assert rq["OriginDestinationInformation"][0]["DestinationLocation"][
        "LocationCode"
    ] == "DEL"


def test_unmapped_fields_absent_from_sabre_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """cabin/currency/refundable/filters have no confirmed mapping:
    they must not leak into the Sabre request."""
    body = build_request(
        monkeypatch,
        home_overrides={
            "cabin": "BUSINESS",
            "currency": "USD",
            "return_date": None,
        },
    )
    serialized = json.dumps(body).lower()

    assert "cabin" not in serialized
    assert "refundable" not in serialized
    assert "airlines" not in serialized


def test_intellisell_transaction_present(monkeypatch: pytest.MonkeyPatch) -> None:
    body = build_request(monkeypatch)
    ext = body["OTA_AirLowFareSearchRQ"]["TPA_Extensions"]

    assert ext == {
        "IntelliSellTransaction": {"RequestType": {"Name": "50ITINS"}}
    }


def test_max_connections_from_filters_when_provided(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = make_settings()
    monkeypatch.setattr(
        "app.transformers.sabre_request_transformer.get_settings",
        lambda: settings,
    )
    body = SabreRequestTransformer().to_provider_request(make_home(), max_connections=0)

    tpa = body["OTA_AirLowFareSearchRQ"]["TravelPreferences"]["TPA_Extensions"]
    assert tpa["MaxConnections"] == {"Number": 0}


def test_max_connections_defaults_to_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = build_request(
        monkeypatch, settings_overrides={"search_max_connections": 2}
    )
    tpa = body["OTA_AirLowFareSearchRQ"]["TravelPreferences"]["TPA_Extensions"]
    assert tpa["MaxConnections"] == {"Number": 2}