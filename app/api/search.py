"""POST /api/flights/search - Java ORBiS -> Python Sabre connector entrypoint.

Receives the common ORBiS flight search payload, decodes and validates via
msgspec, transforms it into the Sabre Bargain Finder Max (OTA_AirLowFareSearchRQ)
payload, calls the Sabre REST API using a bearer token retrieved from Java ORBiS,
and returns the raw Sabre response to Java ORBiS.

Latency notes
-------------
* The blocking Sabre call runs in the Starlette thread pool, so one slow
  provider call cannot stall the event loop (health checks, other searches).
* ``SEARCH_TIMING_LOG=true`` prints a per-stage latency breakdown so it is
  obvious which hop costs what.
"""

from __future__ import annotations

import logging
import time

import msgspec
from fastapi import APIRouter, Depends, Request, Response
from starlette.concurrency import run_in_threadpool

from app.connectors.sabre_client import SabreClient
from app.core.config import get_settings
from app.core.exceptions import TransformationError
from app.schemas.connector import FlightSearchRequest
from app.schemas.home_payload import HomeSearchRequest, Passenger
from app.transformers.router import get_transformer

logger = logging.getLogger(__name__)

router = APIRouter(tags=["flight-search"])

_request_decoder = msgspec.json.Decoder(FlightSearchRequest)

_settings = get_settings()

_sabre_client_instance: SabreClient | None = None


def get_sabre_client() -> SabreClient:
    """Dependency provider for SabreClient."""
    global _sabre_client_instance
    if _sabre_client_instance is None:
        _sabre_client_instance = SabreClient()
    return _sabre_client_instance


def set_sabre_client(client: SabreClient | None) -> None:
    """Override SabreClient instance (useful for testing)."""
    global _sabre_client_instance
    _sabre_client_instance = client


def close_sabre_client() -> None:
    """Close the pooled connections (called from the app lifespan shutdown)."""
    global _sabre_client_instance
    if _sabre_client_instance is not None:
        _sabre_client_instance.close()
        _sabre_client_instance = None


# tripType values that mean "no return leg". ORBiS validates tripType against
# ONE_WAY / ROUND_TRIP / MULTI_CITY, but the connector accepts any spelling so a
# caller that sends "one-way" is not shopped as a round trip by mistake.
_ONE_WAY_TRIP_TYPES = {"ONE_WAY", "ONEWAY"}


def _is_one_way(trip_type: str | None) -> bool:
    """True only when the caller explicitly asked for a one-way trip."""
    normalized = (trip_type or "").strip().upper().replace("-", "_").replace(" ", "_")
    return normalized in _ONE_WAY_TRIP_TYPES


def _json_response(content: bytes, status_code: int) -> Response:
    return Response(
        content=content,
        status_code=status_code,
        media_type="application/json",
    )


@router.post("/api/flights/search")
async def search(
    request: Request,
    client: SabreClient = Depends(get_sabre_client),
) -> Response:
    started = time.perf_counter()

    body = await request.body()
    if not body or len(body.strip()) == 0:
        raise TransformationError("Request body is empty")

    try:
        common_req = _request_decoder.decode(body)
    except msgspec.ValidationError as exc:
        raise TransformationError(f"Invalid common request payload: {exc}") from exc

    if not common_req.from_ or not common_req.to or not common_req.dateFrom:
        raise TransformationError(
            "Missing required search fields: 'from', 'to', and 'dateFrom' are required"
        )

    logger.info(
        "Sabre search request: %s->%s %s..%s tripType=%s ADT=%s CHD=%s INF=%s",
        common_req.from_,
        common_req.to,
        common_req.dateFrom,
        common_req.dateTo,
        common_req.tripType,
        common_req.adt,
        common_req.chd,
        common_req.inf,
    )

    # Build canonical passengers list
    passengers: list[Passenger] = []
    if common_req.adt > 0:
        passengers.append(Passenger(ptc="ADT", count=common_req.adt))
    if common_req.chd > 0:
        passengers.append(Passenger(ptc="CHD", count=common_req.chd))
    if common_req.inf > 0:
        passengers.append(Passenger(ptc="INF", count=common_req.inf))

    # Default to 1 ADT if no passengers were provided
    if not passengers:
        passengers.append(Passenger(ptc="ADT", count=1))

    # A ONE_WAY search must not be shopped as a round trip: a second leg costs
    # Sabre a whole extra set of itineraries to shop and price (roughly double
    # the BFM response time) and returns journeys the caller did not ask for.
    # tripType is authoritative when the caller sends one; when it is absent the
    # previous behaviour (a return leg whenever dateTo is present) is kept.
    return_date = common_req.dateTo
    if _is_one_way(common_req.tripType):
        if return_date:
            logger.info(
                "tripType=ONE_WAY: ignoring dateTo=%s (no return leg is shopped)",
                return_date,
            )
        return_date = None

    home = HomeSearchRequest(
        origin=common_req.from_,
        destination=common_req.to,
        depart_date=common_req.dateFrom,
        return_date=return_date,
        cabin=common_req.cabin,
        currency=common_req.currency,
        promotion_code=common_req.promotionCode,
        passengers=passengers,
    )

    max_connections: int | None = None
    if (
        common_req.filters
        and common_req.filters.stops
        and common_req.filters.stops.max is not None
    ):
        max_connections = common_req.filters.stops.max

    transformer_pair = get_transformer("sabre")

    # Fetch auth data (including PCC) from Java before building the request,
    # so the PCC from the DB can be included in the BFM request body.
    auth_data = client.get_auth_data()
    pcc = auth_data.pcc
    logger.info("Using PCC from Java: %s", pcc)

    transformed_payload = transformer_pair.request.to_provider_request(
        home, max_connections=max_connections, pcc=pcc
    )
    encoded_transformed = msgspec.json.encode(transformed_payload)

    if logger.isEnabledFor(logging.DEBUG):
        logger.debug("Sabre BFM request: %s", encoded_transformed.decode("utf-8"))

    dispatch_at = time.perf_counter()
    resp = await run_in_threadpool(client.search, encoded_transformed, auth_data)

    sabre_finished = time.perf_counter()

    if resp.status_code >= 400:
        logger.warning("Sabre search failed: status=%s", resp.status_code)
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug("Sabre error body: %s", resp.text)

    response = _json_response(resp.content, resp.status_code)

    response_built = time.perf_counter()
    sent_at = time.perf_counter()

    if _settings.search_timing_log:
        logger.info(
            "Sabre pipeline timings:\n"
            "PYTHON: Request received   %5.0f ms\n"
            "PYTHON: Python -> Sabre %5.0f ms\n"
            "PYTHON: Sabre response     %5.0f ms\n"
            "PYTHON: Transformation     %5.0f ms\n"
            "PYTHON: Python -> Java   %5.0f ms",
            (started - started) * 1000,
            (dispatch_at - started) * 1000,
            (sabre_finished - dispatch_at) * 1000,
            (response_built - sabre_finished) * 1000,
            (sent_at - response_built) * 1000,
        )

    return response