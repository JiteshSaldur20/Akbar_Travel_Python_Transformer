"""Application configuration without Pydantic.

Loads settings from environment variables and optional .env file.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


def _load_env_file(env_path: Path) -> dict[str, str]:
    env_vars: dict[str, str] = {}
    if env_path.exists() and env_path.is_file():
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, val = line.split("=", 1)
                env_vars[key.strip().upper()] = val.strip().strip("'\"")
    return env_vars


@dataclass
class Settings:
    """App settings loaded from environment variables and .env file."""

    app_name: str = "Sabre Provider Transformer"
    debug: bool = False
    api_prefix: str = ""

    # Java ORBiS internal service URL (token retrieval)
    java_internal_url: str = "http://localhost:8080"
    java_connect_timeout_s: float = 5.0
    java_read_timeout_s: float = 10.0

    # Sabre NDC connector configuration (REST BFM, OAuth bearer token)
    # PCC comes from Java DB via auth endpoint — not configured here.
    sabre_base_url: str = "https://api.cert.platform.sabre.com"
    sabre_search_path: str = "v5/offers/shop"
    sabre_connect_timeout_s: float = 10.0
    sabre_read_timeout_s: float = 30.0

    # --- Latency tuning ---------------------------------------------------
    # Outbound connections are pooled and kept alive, so a search never pays
    # for a fresh DNS + TCP + TLS handshake on every request.
    http_max_connections: int = 100
    http_max_keepalive_connections: int = 20
    http_keepalive_expiry_s: float = 30.0

    # Refresh the provider token this many seconds before it really expires.
    auth_token_safety_buffer_s: float = 30.0

    # Log a per-stage latency breakdown for every search.
    search_timing_log: bool = True

    # --- BFM request tuning (shapes the ITA request; NOT credentials) ------
    search_number_of_trips: int = 10
    # IntelliSell transaction type: how many itineraries BFM is asked to shop.
    # This is the biggest latency lever on the Sabre side (fewer itineraries =
    # less work), but the value must be one your PCC is provisioned for.
    search_request_type: str = "50ITINS"
    search_max_connections: int = 4
    search_fares_per_journey: int = 4
    search_prefer_ndc_on_tie: bool = False
    search_enable_atpco: bool = True
    search_enable_ndc: bool = False
    search_max_upsells: int = 0
    search_multiple_branded_fares: bool = False


@lru_cache
def get_settings() -> Settings:
    env_file = Path(".env")
    env_dict = _load_env_file(env_file)

    def get_val(name: str, default: str) -> str:
        # Check OS environ first, then .env file, then default
        return os.environ.get(name, env_dict.get(name.upper(), default))

    def get_float(name: str, default: float) -> float:
        val = get_val(name, str(default))
        try:
            return float(val)
        except ValueError:
            return default

    def get_int(name: str, default: int) -> int:
        val = get_val(name, str(default))
        try:
            return int(val)
        except ValueError:
            return default

    def get_bool(name: str, default: bool) -> bool:
        val = get_val(name, str(default)).lower()
        return val in ("true", "1", "yes", "on")

    return Settings(
        app_name=get_val("APP_NAME", "Sabre Provider Transformer"),
        debug=get_bool("DEBUG", False),
        api_prefix=get_val("API_PREFIX", ""),
        java_internal_url=get_val("JAVA_INTERNAL_URL", "http://localhost:8080"),
        java_connect_timeout_s=get_float("JAVA_CONNECT_TIMEOUT_S", 5.0),
        java_read_timeout_s=get_float("JAVA_READ_TIMEOUT_S", 10.0),
        sabre_base_url=get_val("SABRE_BASE_URL", "https://api.cert.platform.sabre.com"),
        sabre_search_path=get_val("SABRE_SEARCH_PATH", "v5/offers/shop"),
        sabre_connect_timeout_s=get_float("SABRE_CONNECT_TIMEOUT_S", 10.0),
        sabre_read_timeout_s=get_float("SABRE_READ_TIMEOUT_S", 30.0),
        http_max_connections=get_int("HTTP_MAX_CONNECTIONS", 100),
        http_max_keepalive_connections=get_int("HTTP_MAX_KEEPALIVE_CONNECTIONS", 20),
        http_keepalive_expiry_s=get_float("HTTP_KEEPALIVE_EXPIRY_S", 30.0),
        auth_token_safety_buffer_s=get_float("AUTH_TOKEN_SAFETY_BUFFER_S", 30.0),
        search_timing_log=get_bool("SEARCH_TIMING_LOG", True),
        search_number_of_trips=get_int("SEARCH_NUMBER_OF_TRIPS", 10),
        search_request_type=get_val("SEARCH_REQUEST_TYPE", "50ITINS"),
        search_max_connections=get_int("SEARCH_MAX_CONNECTIONS", 4),
        search_fares_per_journey=get_int("SEARCH_FARES_PER_JOURNEY", 4),
        search_prefer_ndc_on_tie=get_bool("SEARCH_PREFER_NDC_ON_TIE", False),
        search_enable_atpco=get_bool("SEARCH_ENABLE_ATPCO", True),
        search_enable_ndc=get_bool("SEARCH_ENABLE_NDC", False),
        search_max_upsells=get_int("SEARCH_MAX_UPSELLS", 0),
        search_multiple_branded_fares=get_bool("SEARCH_MULTIPLE_BRANDED_FARES", False),
    )