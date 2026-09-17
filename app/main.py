"""FastAPI application entrypoint.

Run locally with the venv:
    .venv/Scripts/python -m uvicorn app.main:app --reload
"""

import logging
from contextlib import asynccontextmanager
from typing import Iterator

from fastapi import FastAPI

from app.api.search import close_sabre_client
from app.api.search import router as search_router
from app.core.config import get_settings
from app.core.exceptions import register_exception_handlers
from app.transformers.router import known_providers


def _configure_logging(debug: bool) -> None:
    """Make the app's own log records visible under plain uvicorn.

    Uvicorn only configures its own loggers, so without this the root logger
    stays at WARNING and ``logger.info(...)`` from the app would be dropped.
    """
    level = logging.DEBUG if debug else logging.INFO

    if not logging.getLogger().handlers:
        logging.basicConfig(
            level=level,
            format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        )

    logging.getLogger("app").setLevel(level)


def create_app() -> FastAPI:
    settings = get_settings()
    _configure_logging(settings.debug)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> Iterator[None]:
        yield
        # Release pooled provider connections on shutdown.
        close_sabre_client()

    app = FastAPI(
        title=settings.app_name,
        version="1.1.0",
        debug=settings.debug,
        lifespan=lifespan,
    )
    register_exception_handlers(app)
    app.include_router(search_router, prefix=settings.api_prefix)

    @app.get("/health")
    async def health() -> dict[str, object]:
        return {
            "status": "ok",
            "providers": known_providers(),
        }

    return app


app = create_app()