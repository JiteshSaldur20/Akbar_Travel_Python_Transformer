"""Request transformer: Home Payload -> Sabre BFM request payload.

Only confirmed mappings are implemented (see project Postman collection
"BFM v5" request and Java's FlightSearchParameters). Unconfirmed fields
are marked TODO and not invented.

The transformed request deliberately contains NO provider credentials:
no PCC / PseudoCityCode, no tokens. Java injects credentials (PCC in
POS, Authorization header) when it makes the actual Sabre API call.

cabin, currency, refundable, and filters are validated as part of the
Home Payload but intentionally unmapped (no confirmed Sabre mapping).
"""

from typing import Any

from app.config.settings import Settings
from app.models.home_payload import HomePayload


def transform_request(payload: HomePayload, settings: Settings) -> dict[str, Any]:
    """Transform the common Home Payload into a Sabre BFM request body.

    - ONE_WAY: 1 leg (from -> to on dateFrom)
    - ROUND_TRIP: 2 legs (from -> to on dateFrom, to -> from on dateTo)
    - MULTI_CITY: not supported by this mapping yet (TODO)

    Every value comes from the received payload or service settings;
    nothing is hardcoded and no state is retained between requests.
    """
    origin_destination = _build_origin_destination_information(payload)

    # PCC/PseudoCityCode is intentionally NOT set here: Java owns provider
    # credentials and injects POS.Source[0].PseudoCityCode before calling
    # Sabre. RequestorID shape is confirmed by the project Postman collection.
    pos = {
        "Source": [
            {
                "RequestorID": {
                    "Type": "1",
                    "ID": "1",
                    "CompanyName": {"Code": "TN"},
                },
            }
        ]
    }

    tpa: dict[str, Any] = {
        "NumTrips": {"Number": settings.search_number_of_trips},
        "DataSources": {
            "ATPCO": "Enable" if settings.search_enable_atpco else "Disable",
            "NDC": "Enable" if settings.search_enable_ndc else "Disable",
        },
        "PreferNDCSourceOnTie": {"Value": settings.search_prefer_ndc_on_tie},
        "NDCIndicators": {
            "MultipleBrandedFares": {
                "Value": settings.search_multiple_branded_fares
            },
            "MaxNumberOfUpsells": {"Value": settings.search_max_upsells},
        },
    }

    traveler_summary = _build_traveler_info_summary(payload)

    request_body: dict[str, Any] = {
        "OTA_AirLowFareSearchRQ": {
            "Version": "5",
            "POS": pos,
            "OriginDestinationInformation": origin_destination,
            "TravelPreferences": {"TPA_Extensions": tpa},
            "TravelerInfoSummary": traveler_summary,
            "TPA_Extensions": {
                "IntelliSellTransaction": {
                    "RequestType": {"Name": "50ITINS"}
                }
            },
        }
    }

    return request_body


def _build_origin_destination_information(
    payload: HomePayload,
) -> list[dict[str, Any]]:
    """Build the OriginDestinationInformation array per trip type."""
    legs: list[dict[str, Any]] = []

    outbound = {
        "RPH": "1",
        "DepartureDateTime": f"{payload.date_from.isoformat()}T00:00:00",
        "OriginLocation": {
            "LocationCode": payload.from_location.strip().upper()
        },
        "DestinationLocation": {
            "LocationCode": payload.to_location.strip().upper()
        },
    }
    legs.append(outbound)

    if payload.trip_type == "ROUND_TRIP":
        inbound = {
            "RPH": "2",
            "DepartureDateTime": f"{payload.date_to.isoformat()}T00:00:00",
            "OriginLocation": {
                "LocationCode": payload.to_location.strip().upper()
            },
            "DestinationLocation": {
                "LocationCode": payload.from_location.strip().upper()
            },
        }
        legs.append(inbound)

    if payload.trip_type == "MULTI_CITY":
        # TODO: MULTI_CITY leg mapping is not confirmed for this connector
        # yet; the home payload has no per-leg structure. Only outbound is
        # sent, which the provider may reject. Do not invent leg structure.
        pass

    return legs


def _build_traveler_info_summary(payload: HomePayload) -> dict[str, Any]:
    """Map ADT/CHD/INF quantities; zero-count types are omitted."""
    ptq: list[dict[str, Any]] = []

    for code, quantity in payload.passengers_by_type().items():
        ptq.append({"Code": code, "Quantity": quantity})

    return {"AirTravelerAvail": [{"PassengerTypeQuantity": ptq}]}
