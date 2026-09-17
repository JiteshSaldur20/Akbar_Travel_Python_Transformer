"""FastAPI layer: HTTP endpoints for the transformation-only service.

FastAPI is only the HTTP framework. Body decoding/validation is done
with msgspec; the response is the raw Sabre request payload that Java
should POST to Sabre. There are NO outbound API calls from this
service: Python transforms, Java calls Sabre.

Endpoint: POST /api/flights/search
"""

from typing import Any

import msgspec
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.config.settings import Settings, get_settings
from app.exceptions.integration import ConnectorError
from app.logging_config import configure_logging, get_logger
from app.services.flight_search import FlightSearchService

logger = get_logger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    """Application factory (used by prod entrypoint and tests)."""
    resolved_settings = settings or get_settings()
    configure_logging()

    app = FastAPI(
        title="Sabre Request Transformer",
        description=(
            "Transformation-only provider connector service: receives the "
            "common Home Payload from Java and returns the Sabre request "
            "payload. Java makes the actual Sabre API request and owns "
            "credentials/authentication."
        ),
        version="2.0.0",
    )

    search_service = FlightSearchService(resolved_settings)

    # Stored on state so tests can swap in a mocked service.
    app.state.settings = resolved_settings
    app.state.search_service = search_service

    @app.post("/api/flights/search")
    async def search_flights(request: Request) -> JSONResponse:
        """Transform a Home Payload into the Sabre request payload."""
        body = await request.body()
        return _run_transform(request.app.state.search_service, body)

    @app.get("/health")
    def health(request: Request) -> dict[str, str]:
        """Liveness probe."""
        return {
            "status": "UP",
            "service": request.app.state.settings.service_name,
        }

    @app.exception_handler(ConnectorError)
    async def connector_error_handler(
        _request: Request, exc: ConnectorError
    ) -> JSONResponse:
        """Map controlled transformation errors to controlled responses."""
        logger.error("Transformation error: %s (%s)", exc.message, exc.error_code)
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "success": False,
                "errorCode": exc.error_code,
                "message": exc.message,
            },
        )

    return app


def _run_transform(
    search_service: FlightSearchService,
    body: bytes,
) -> JSONResponse:
    """Run the transformation pipeline and return the raw Sabre body."""
    try:
        result = search_service.transform(body)
    except ConnectorError:
        raise  # handled by the registered exception handler
    except Exception:
        logger.exception("Unexpected error during transformation")
        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "errorCode": "INTERNAL_ERROR",
                "message": "Unexpected transformation error",
            },
        )

    return JSONResponse(status_code=200, content=result)


app = create_app()
