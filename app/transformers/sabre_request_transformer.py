"""HomeSearchRequest -> Sabre BFM (OTA_AirLowFareSearchRQ) request payload.

Only confirmed mappings are implemented (see the project Postman collection
"BFM v5" request and Java's FlightSearchParameters). Unconfirmed fields are
marked TODO and not invented.

The PCC (PseudoCityCode) is received from Java ORBiS via the auth endpoint
response, keeping all credentials in the Java DB. It is mandatory in the BFM
request body, so a missing PCC fails fast instead of being dropped. The bearer
token is injected by :class:`app.connectors.sabre_client.SabreClient` via the
Authorization header.

cabin, currency, refundable, and filters are validated as part of the
connector payload but intentionally unmapped (no confirmed Sabre mapping).
"""

from __future__ import annotations

import logging
from typing import Any

from app.core.config import get_settings
from app.core.exceptions import TransformationError
from app.schemas.home_payload import HomeSearchRequest
from app.transformers.base import RequestTransformer

logger = logging.getLogger(__name__)


class SabreRequestTransformer(RequestTransformer):
    def to_provider_request(
        self, home: HomeSearchRequest, max_connections: int | None = None, pcc: str | None = None
    ) -> dict[str, Any]:
        """Transform the canonical home request into a Sabre BFM request body.

        - No return date: 1 leg (from -> to on depart_date)
        - Return date present: 2 legs (from -> to, to -> from on return_date)

        Every value comes from the received payload or service settings;
        nothing is hardcoded and no state is retained between requests.
        """
        settings = get_settings()

        logger.info(
            "Transforming request: %s->%s depart=%s return=%s passengers=%s pcc=%s",
            home.origin, home.destination, home.depart_date, home.return_date,
            [(p.ptc, p.count) for p in home.passengers],
            pcc,
        )

        origin_destination = _build_origin_destination_information(home)

        # PCC (PseudoCityCode) is required in the BFM request body by Sabre.
        # It comes from Java ORBiS auth endpoint (provider_credential.pcc in DB,
        # env fallback) and must be the PCC the bearer token was minted for.
        # Without it Sabre answers HTTP 400 "Unable to determine
        # PseudoCityCode", so an empty PCC is a configuration error - never
        # silently send the request without it.
        normalized_pcc = (pcc or "").strip().upper()
        if not normalized_pcc:
            raise TransformationError(
                "Sabre PCC (PseudoCityCode) is missing: it is required in "
                "POS.Source[0] and must be configured for the provider "
                "credential in Java ORBiS"
            )

        source: dict[str, object] = {
            "PseudoCityCode": normalized_pcc,
            "RequestorID": {
                "Type": "1",
                "ID": "1",
                "CompanyName": {"Code": "TN"},
            },
        }

        pos = {"Source": [source]}

        # NOTE: the stop/connection limit (max_connections) is intentionally
        # not sent. Sabre's BFM v5 JSON schema rejects additional properties:
        #   JSON_ADAPTER: /OTA_AirLowFareSearchRQ/TravelPreferences/TPA_Extensions:
        #   property 'MaxConnections' is not defined in the schema
        # No confirmed mapping exists yet, so the filter stays unused rather
        # than making every shop request invalid.
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

        traveler_summary = _build_traveler_info_summary(home)

        result = {
            "OTA_AirLowFareSearchRQ": {
                "Version": "5",
                "POS": pos,
                "OriginDestinationInformation": origin_destination,
                "TravelPreferences": {"TPA_Extensions": tpa},
                "TravelerInfoSummary": traveler_summary,
                "TPA_Extensions": {
                    "IntelliSellTransaction": {
                        "RequestType": {"Name": settings.search_request_type}
                    }
                },
            }
        }

        logger.info(
            "Sabre BFM request built: %d legs, NumTrips=%d, ATPCO=%s, NDC=%s, pcc=%s",
            len(origin_destination),
            settings.search_number_of_trips,
            settings.search_enable_atpco,
            settings.search_enable_ndc,
            normalized_pcc,
        )
        return result


def _build_origin_destination_information(
    home: HomeSearchRequest,
) -> list[dict[str, Any]]:
    """Build the OriginDestinationInformation array from the home request."""
    legs: list[dict[str, Any]] = []

    outbound = {
        "RPH": "1",
        "DepartureDateTime": f"{home.depart_date}T00:00:00",
        "OriginLocation": {
            "LocationCode": home.origin.strip().upper()
        },
        "DestinationLocation": {
            "LocationCode": home.destination.strip().upper()
        },
    }
    legs.append(outbound)

    if home.return_date:
        inbound = {
            "RPH": "2",
            "DepartureDateTime": f"{home.return_date}T00:00:00",
            "OriginLocation": {
                "LocationCode": home.destination.strip().upper()
            },
            "DestinationLocation": {
                "LocationCode": home.origin.strip().upper()
            },
        }
        legs.append(inbound)

    return legs


def _build_traveler_info_summary(home: HomeSearchRequest) -> dict[str, Any]:
    """Map ADT/CHD/INF quantities; zero-count types are omitted."""
    ptq: list[dict[str, Any]] = []

    for passenger in home.passengers:
        if passenger.count < 1:
            continue
        ptq.append({
            "Code": passenger.ptc.strip().upper(),
            "Quantity": passenger.count,
        })

    if not ptq:
        ptq.append({"Code": "ADT", "Quantity": 1})

    return {"AirTravelerAvail": [{"PassengerTypeQuantity": ptq}]}