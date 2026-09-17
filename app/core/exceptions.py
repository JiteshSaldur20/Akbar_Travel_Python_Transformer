"""Domain exceptions and their HTTP error responses."""

import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)


class ProviderTransformerError(Exception):
    """Base class for all app errors."""


class UnknownProviderError(ProviderTransformerError):
    def __init__(self, provider: str, known: list[str]) -> None:
        self.provider = provider
        self.known = known
        super().__init__(
            f"Unknown provider {provider!r}. Known providers: {', '.join(known)}"
        )


class TransformationError(ProviderTransformerError):
    """Raised when a payload cannot be converted to/from a provider format."""


class ProviderApiError(ProviderTransformerError):
    """Raised when communicating with a downstream provider API fails."""

    def __init__(
        self,
        message: str,
        status_code: int = 502,
        provider_error: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.provider_error = provider_error


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(UnknownProviderError)
    async def unknown_provider_handler(
        request: Request, exc: UnknownProviderError
    ) -> JSONResponse:
        logger.warning("Unknown provider requested: %s (known: %s)", exc.provider, exc.known)
        return JSONResponse(
            status_code=404,
            content={
                "error": "unknown_provider",
                "message": str(exc),
                "known_providers": exc.known,
            },
        )

    @app.exception_handler(TransformationError)
    async def transformation_error_handler(
        request: Request, exc: TransformationError
    ) -> JSONResponse:
        logger.error("Transformation error: %s", exc)
        return JSONResponse(
            status_code=422,
            content={"error": "transformation_failed", "message": str(exc)},
        )

    @app.exception_handler(ProviderApiError)
    async def provider_api_error_handler(
        request: Request, exc: ProviderApiError
    ) -> JSONResponse:
        logger.error("Provider API error (status=%d): %s", exc.status_code, exc.message)
        if exc.provider_error:
            logger.debug("Provider error detail: %s", exc.provider_error[:2000])
        content: dict[str, object] = {
            "error": "provider_api_error",
            "message": exc.message,
            "status": exc.status_code,
        }
        if exc.provider_error is not None:
            content["providerError"] = exc.provider_error
        return JSONResponse(
            status_code=exc.status_code,
            content=content,
        )