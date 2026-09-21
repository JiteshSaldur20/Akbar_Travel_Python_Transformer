"""Transform endpoints (default msgspec engine).

Two bare-payload endpoints per provider:

- POST /transform/{provider}          : canonical home payload -> provider request
- POST /transform/{provider}/response : provider response payload -> home response

FastAPI's Pydantic validation is bypassed for bodies (response_model=None)
since msgspec Structs are not Pydantic models — all decode/encode goes
through msgspec instead.

Sabre's request transform needs the PCC (it is mandatory in the BFM body), and
the normal search path reads it from Java ORBiS. A transform is a pure function
of its inputs, so the PCC is taken as a query parameter here rather than by
calling out to Java: without it the request transformer fails fast with the
usual "PCC is missing" error instead of building an invalid BFM body.

Performance notes:
- Responses are encoded with msgspec (several times faster than
  JSONResponse's stdlib json) and returned as pre-encoded bytes.
- Request transforms are pure functions of the payload, so the whole
  decode -> transform -> encode pipeline is memoized with a bounded
  LRU cache keyed on (provider, raw body, pcc, max_connections). Identical
  payloads (very common in production traffic) become near-zero-cost lookups
  while memory stays bounded.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

import msgspec
from fastapi import APIRouter, Request
from fastapi.responses import Response

from app.core.exceptions import TransformationError
from app.schemas.home_payload import HomeSearchRequest
from app.transformers.router import TransformerPair, get_transformer, known_providers

router = APIRouter(tags=["transform"])

# Bound the cache so memory cannot grow without limit.
_CACHE_SIZE = 4096


@lru_cache(maxsize=_CACHE_SIZE)
def _encode_provider_request(
    provider: str,
    body: bytes,
    pcc: str | None,
    max_connections: int | None,
) -> bytes:
    """Decode a home payload, transform it for `provider`, encode JSON bytes.

    Cached on the inputs: repeated identical requests skip decoding,
    transformation, and encoding entirely.
    """
    pair = get_transformer(provider)  # raises UnknownProviderError -> 404

    try:
        home = msgspec.json.decode(body, type=HomeSearchRequest)
    except msgspec.ValidationError as exc:
        raise TransformationError(f"Invalid home payload: {exc}") from exc

    return msgspec.json.encode(
        pair.request.to_provider_request(
            home, max_connections=max_connections, pcc=pcc
        )
    )


def _pair_or_404(provider: str) -> TransformerPair:
    return get_transformer(provider)


@router.post("/transform/{provider}", response_model=None)
async def transform(
    provider: str,
    request: Request,
    pcc: str | None = None,
    max_connections: int | None = None,
) -> Response:
    """Convert a canonical home request into the provider's request format."""
    body = await request.body()
    if not body:
        raise TransformationError("Request body is empty")

    # Return the provider payload directly (no wrapper), pre-encoded.
    return Response(
        content=_encode_provider_request(provider, body, pcc, max_connections),
        media_type="application/json",
    )


@router.post("/transform/{provider}/response", response_model=None)
async def transform_response(provider: str, request: Request) -> Response:
    """Convert a provider's response payload back into the canonical home format."""
    body = await request.body()
    if not body:
        raise TransformationError("Request body is empty")

    pair = _pair_or_404(provider)

    try:
        payload: Any = msgspec.json.decode(body)
    except msgspec.DecodeError as exc:
        raise TransformationError(f"Invalid JSON: {exc}") from exc

    result = pair.response.to_home_response(payload)
    # msgspec Struct -> JSON bytes (much faster than to_builtins + stdlib json)
    return Response(
        content=msgspec.json.encode(result),
        media_type="application/json",
    )


@router.get("/providers")
async def providers() -> dict[str, list[str]]:
    """List the registered providers."""
    return {"providers": known_providers()}
