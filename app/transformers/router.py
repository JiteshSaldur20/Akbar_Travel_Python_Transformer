"""Provider transformer base class and registry.

This connector is Sabre-only. To add a new provider later:

1. Create ``app/transformers/<provider>.py`` with a ``TransformerPair``.
2. Subclass :class:`RequestTransformer` / :class:`ResponseTransformer`.
3. Register the pair in :data:`PROVIDER_TRANSFORMERS`.
"""

from __future__ import annotations

from app.core.exceptions import UnknownProviderError
from app.transformers.base import (
    RequestTransformer,
    ResponseTransformer,
    TransformerPair,
)

__all__ = [
    "RequestTransformer",
    "ResponseTransformer",
    "TransformerPair",
    "known_providers",
    "get_transformer",
]

# --- Registry -----------------------------------------------------------
# The provider module is imported here (not at module top) so it resolves
# against fully-defined ABCs from ``app.transformers.base``.

from app.transformers.sabre import SABRE  # noqa: E402

PROVIDER_TRANSFORMERS: dict[str, TransformerPair] = {
    "sabre": SABRE,
}


def known_providers() -> list[str]:
    return sorted(PROVIDER_TRANSFORMERS)


def get_transformer(provider: str) -> TransformerPair:
    key = provider.strip().lower()
    pair = PROVIDER_TRANSFORMERS.get(key)
    if pair is None:
        raise UnknownProviderError(provider, known_providers())
    return pair