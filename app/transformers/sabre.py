"""Sabre provider transformers."""

from app.transformers.base import TransformerPair
from app.transformers.sabre_request_transformer import SabreRequestTransformer
from app.transformers.sabre_response_transformer import SabreResponseTransformer

SABRE = TransformerPair(
    request=SabreRequestTransformer(),
    response=SabreResponseTransformer(),
)

__all__ = ["SABRE"]