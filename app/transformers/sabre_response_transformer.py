"""Sabre response transformer (stub).

The flight-search connector returns Sabre's raw BFM response to Java ORBiS
byte-for-byte, so no canonical conversion is needed on the search path. This
conversion endpoint is provided for contract completeness only; implement it
when a canonical Sabre -> HomeSearchResponse mapping is required.
"""

from __future__ import annotations

from typing import Any

from app.core.exceptions import TransformationError
from app.schemas.home_payload import HomeSearchResponse
from app.transformers.base import ResponseTransformer


class SabreResponseTransformer(ResponseTransformer):
    def to_home_response(self, payload: dict[str, Any]) -> HomeSearchResponse:
        raise TransformationError(
            "Sabre response transformation is not implemented; the raw "
            "response is returned to Java ORBiS"
        )