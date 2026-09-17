"""Transformers base: abstract converters + pair container.

Sitting in its own module so provider transformer modules can import the ABCs
without triggering a circular import through ``router.py`` (which imports the
provider module that imports these).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from app.schemas.home_payload import HomeSearchRequest, HomeSearchResponse


class RequestTransformer(ABC):
    """Converts a canonical home request into a provider-specific payload."""

    @abstractmethod
    def to_provider_request(
        self, home: HomeSearchRequest, max_connections: int | None = None
    ) -> dict[str, Any]:
        """HomeSearchRequest -> provider request payload (dict)."""


class ResponseTransformer(ABC):
    """Converts a provider-specific response into the canonical home response."""

    @abstractmethod
    def to_home_response(self, payload: dict[str, Any]) -> HomeSearchResponse:
        """Provider response payload (dict) -> HomeSearchResponse."""


class TransformerPair:
    """A provider's request + response transformers."""

    def __init__(
        self,
        request: RequestTransformer,
        response: ResponseTransformer,
    ) -> None:
        self.request = request
        self.response = response