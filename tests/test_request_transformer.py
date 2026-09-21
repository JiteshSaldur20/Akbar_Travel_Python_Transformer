"""Tests for the Sabre request transformer (home request -> Sabre BFM).

Uses the canonical home request model that Java ORBiS forwards to every
provider microservice.
"""

from __future__ import annotations

import json

import pytest

from app.core.config import Settings
from app.core.exceptions import TransformationError
from app.schemas.home_payload import HomeSearchRequest, Passenger
from app.transformers.sabre_request_transformer import SabreRequestTransformer

TEST_PCC = "86AD"


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
    pcc: str | None = TEST_PCC,
) -> dict:
    """Build a Sabre BFM body from a home request + settings override."""
    settings = make_settings(**(settings_overrides or {}))
    monkeypatch.setattr(
        "app.transformers.sabre_request_transformer.get_settings",
        lambda: settings,
    )
    return SabreRequestTransformer().to_provider_request(
        make_home(**(home_overrides or {})), pcc=pcc
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


def test_pos_carries_pcc_but_no_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The BFM body must carry the PCC but no credentials/tokens (auth is a header)."""
    body = build_request(monkeypatch)
    serialized = json.dumps(body).lower()

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
    # Sabre rejects the request with HTTP 400 "Unable to determine
    # PseudoCityCode" when this is absent.
    assert source["PseudoCityCode"] == TEST_PCC


def test_pcc_is_trimmed_and_uppercased(monkeypatch: pytest.MonkeyPatch) -> None:
    body = build_request(monkeypatch, pcc=" 86ad ")
    source = body["OTA_AirLowFareSearchRQ"]["POS"]["Source"][0]

    assert source["PseudoCityCode"] == TEST_PCC


@pytest.mark.parametrize("pcc", [None, "", "   "])
def test_missing_pcc_fails_fast(
    monkeypatch: pytest.MonkeyPatch, pcc: str | None
) -> None:
    """A BFM request without a PCC is always rejected by Sabre, so never send it."""
    with pytest.raises(TransformationError, match="PseudoCityCode"):
        build_request(monkeypatch, pcc=pcc)


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


def test_intellisell_transaction_defaults_to_50itins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = build_request(monkeypatch)
    ext = body["OTA_AirLowFareSearchRQ"]["TPA_Extensions"]

    assert ext == {
        "IntelliSellTransaction": {"RequestType": {"Name": "50ITINS"}}
    }


def test_intellisell_request_type_is_configurable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The ITINS bucket is the main BFM latency lever, so it is tunable via
    SEARCH_REQUEST_TYPE without a code change."""
    body = build_request(
        monkeypatch, settings_overrides={"search_request_type": "20ITINS"}
    )
    ext = body["OTA_AirLowFareSearchRQ"]["TPA_Extensions"]

    assert ext["IntelliSellTransaction"] == {"RequestType": {"Name": "20ITINS"}}


@pytest.mark.parametrize("max_connections", [None, 0, 2])
def test_stop_limit_is_not_sent_to_sabre(
    monkeypatch: pytest.MonkeyPatch, max_connections: int | None
) -> None:
    """Sabre's BFM v5 schema rejects additional TPA_Extensions properties:

        JSON_ADAPTER: /OTA_AirLowFareSearchRQ/TravelPreferences/TPA_Extensions:
        property 'MaxConnections' is not defined in the schema

    so the stop filter must not leak into the request until a confirmed
    mapping exists.
    """
    settings = make_settings()
    monkeypatch.setattr(
        "app.transformers.sabre_request_transformer.get_settings",
        lambda: settings,
    )
    body = SabreRequestTransformer().to_provider_request(
        make_home(), max_connections=max_connections, pcc=TEST_PCC
    )

    tpa = body["OTA_AirLowFareSearchRQ"]["TravelPreferences"]["TPA_Extensions"]
    assert "MaxConnections" not in tpa
    assert "MaxStopsQuantity" not in tpa
    assert "maxConnections" not in json.dumps(body)