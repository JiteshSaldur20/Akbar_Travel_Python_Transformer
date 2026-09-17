"""Service configuration (transformation-only Python service).

msgspec Struct loaded from environment variables / .env file.

Sabre credentials, PCC, provider URLs, tokens, and provider HTTP
settings are NOT configured here: this service never contacts Sabre.
Java owns provider communication and authentication.
"""

import os
from functools import lru_cache
from pathlib import Path

import msgspec


def _load_dotenv(path: str = ".env") -> None:
    """Minimal .env loader (KEY=VALUE lines); real env wins."""
    env_file = Path(path)
    if not env_file.exists():
        return

    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _env_str(name: str, default: str) -> str:
    value = os.environ.get(name)
    return value if value not in (None, "") else default


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    return default if raw in (None, "") else int(raw)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw in (None, ""):
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


class Settings(msgspec.Struct):
    """Service-level settings only. No Sabre credentials or URLs."""

    # --- Service -------------------------------------------------------
    service_name: str = "sabre-transformer"
    service_port: int = 8090

    # --- Search tuning (mirrors Java-side FlightSearchParameters) -------
    # These shape the BFM request structure; they are NOT credentials.
    search_number_of_trips: int = 10
    search_max_connections: int = 4
    search_fares_per_journey: int = 4
    search_prefer_ndc_on_tie: bool = True
    search_enable_atpco: bool = True
    search_enable_ndc: bool = False
    search_max_upsells: int = 4
    search_multiple_branded_fares: bool = True


@lru_cache
def get_settings() -> Settings:
    """Return cached settings; environment is read once."""
    _load_dotenv()
    return _build_settings()


def _build_settings() -> Settings:
    """Construct Settings from the environment (after .env load)."""
    return Settings(
        service_name=_env_str("SERVICE_NAME", "sabre-transformer"),
        service_port=_env_int("SERVICE_PORT", 8090),
        search_number_of_trips=_env_int("SEARCH_NUMBER_OF_TRIPS", 10),
        search_max_connections=_env_int("SEARCH_MAX_CONNECTIONS", 4),
        search_fares_per_journey=_env_int("SEARCH_FARES_PER_JOURNEY", 4),
        search_prefer_ndc_on_tie=_env_bool("SEARCH_PREFER_NDC_ON_TIE", True),
        search_enable_atpco=_env_bool("SEARCH_ENABLE_ATPCO", True),
        search_enable_ndc=_env_bool("SEARCH_ENABLE_NDC", False),
        search_max_upsells=_env_int("SEARCH_MAX_UPSELLS", 4),
        search_multiple_branded_fares=_env_bool(
            "SEARCH_MULTIPLE_BRANDED_FARES", True
        ),
    )
