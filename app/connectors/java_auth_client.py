"""Reusable client to retrieve valid provider access tokens from Java ORBiS.

Latency notes
-------------
* One ``httpx.Client`` lives for the whole object lifetime, so the connection
  to Java ORBiS stays open between searches instead of paying a new DNS + TCP
  handshake on every call.
* Tokens are memoized in-process and refreshed shortly before they expire, so
  the common case costs zero HTTP round trips (and zero DB queries on the Java
  side).
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta, timezone

import httpx
import msgspec

from app.core.config import Settings, get_settings
from app.core.exceptions import ProviderApiError

logger = logging.getLogger(__name__)


class JavaProviderAuthResponse(msgspec.Struct, kw_only=True):
    providerCode: str
    accessToken: str
    tokenType: str = "Bearer"
    expiresAt: str | None = None
    pcc: str | None = None


_auth_decoder = msgspec.json.Decoder(JavaProviderAuthResponse)


def _parse_expiry(value: str | None) -> datetime | None:
    """Parse the ISO-8601 instant sent by Java (``2026-10-01T12:00:00Z``)."""
    if not value:
        return None

    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"

    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)

    return parsed


class JavaAuthClient:
    """Client for querying Java ORBiS internal authentication endpoint."""

    def __init__(
        self,
        settings: Settings | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self._transport = transport

        self._http = httpx.Client(
            transport=transport,
            timeout=httpx.Timeout(
                self.settings.java_read_timeout_s,
                connect=self.settings.java_connect_timeout_s,
            ),
            limits=httpx.Limits(
                max_connections=self.settings.http_max_connections,
                max_keepalive_connections=self.settings.http_max_keepalive_connections,
                keepalive_expiry=self.settings.http_keepalive_expiry_s,
            ),
            headers={"Accept": "application/json"},
        )

        self._lock = threading.Lock()
        self._cached_token: JavaProviderAuthResponse | None = None
        self._cached_until: datetime | None = None
        self._cached_key: str | None = None

        logger.info(
            "JavaAuthClient initialized (java_url=%s, connect_timeout=%.1fs, read_timeout=%.1fs)",
            self.settings.java_internal_url,
            self.settings.java_connect_timeout_s,
            self.settings.java_read_timeout_s,
        )

    def close(self) -> None:
        """Release the pooled connections held by this client."""
        logger.info("Closing JavaAuthClient pooled connections")
        self._http.close()

    def _valid_cached_token(self, cache_key: str) -> JavaProviderAuthResponse | None:
        token = self._cached_token
        if token is None or self._cached_until is None:
            return None
        if self._cached_key != cache_key:
            return None
        if datetime.now(timezone.utc) >= self._cached_until:
            logger.info("Cached token expired for key=%s", cache_key)
            return None
        return token

    def get_token(
        self, provider_code: str, api_type: str | None = None
    ) -> JavaProviderAuthResponse:
        """Return a valid token, reusing the cached one while it is still fresh."""
        code = provider_code.strip().upper()
        cache_key = f"{code}|{(api_type or '').strip().lower()}"

        cached = self._valid_cached_token(cache_key)
        if cached is not None:
            logger.info("Using cached token for %s (valid until %s)", cache_key, self._cached_until)
            return cached

        logger.info("No valid cached token for %s, fetching from Java ORBiS...", cache_key)
        t0 = time.perf_counter()
        auth_data = self._fetch_token(code, api_type)
        t1 = time.perf_counter()
        logger.info("Token fetched for %s in %.0f ms", cache_key, (t1 - t0) * 1000)

        expires_at = _parse_expiry(auth_data.expiresAt)
        if expires_at is not None:
            usable_until = expires_at - timedelta(
                seconds=self.settings.auth_token_safety_buffer_s
            )
            if usable_until > datetime.now(timezone.utc):
                with self._lock:
                    self._cached_token = auth_data
                    self._cached_until = usable_until
                    self._cached_key = cache_key
                logger.info("Token cached for %s (usable until %s)", cache_key, usable_until)
            else:
                logger.warning("Token for %s already near expiry, not caching", cache_key)
        else:
            logger.warning("No expiresAt in token response for %s, cannot cache", cache_key)

        return auth_data

    def _fetch_token(
        self, code: str, api_type: str | None
    ) -> JavaProviderAuthResponse:
        base = self.settings.java_internal_url.rstrip("/")
        url = f"{base}/internal/providers/{code}/auth"

        params: dict[str, str] = {}
        if api_type:
            params["apiType"] = api_type

        logger.info("Requesting token: GET %s params=%s", url, params)

        try:
            resp = self._http.get(url, params=params)
        except httpx.HTTPError as exc:
            logger.error("Java ORBiS auth request failed: %s", exc)
            raise ProviderApiError(
                f"Failed to connect to Java ORBiS internal auth service at {url}: {exc}",
                status_code=502,
            ) from exc

        logger.info("Java auth response: status=%d, size=%d bytes", resp.status_code, len(resp.content))

        if resp.status_code != 200:
            error_msg = f"Java auth endpoint returned HTTP {resp.status_code}"
            logger.error("Auth failed for %s: %s", code, error_msg)
            if logger.isEnabledFor(logging.DEBUG):
                logger.debug("Auth error body: %s", resp.text[:2000])
            raise ProviderApiError(
                f"Failed to retrieve {code} token from Java: {error_msg}",
                status_code=resp.status_code if 400 <= resp.status_code < 500 else 502,
                provider_error=resp.text,
            )

        try:
            auth_data = _auth_decoder.decode(resp.content)
        except msgspec.ValidationError as exc:
            logger.error("Failed to decode Java auth response for %s: %s", code, exc)
            raise ProviderApiError(
                f"Failed to parse Java auth response for {code}: {exc}",
                status_code=502,
                provider_error=resp.text,
            ) from exc

        if not auth_data.accessToken or auth_data.accessToken.strip() == "":
            logger.error("Empty accessToken returned for %s", code)
            raise ProviderApiError(
                f"Java auth endpoint returned an empty accessToken for {code}",
                status_code=502,
            )

        logger.info("Valid token received for %s (tokenType=%s)", code, auth_data.tokenType)
        return auth_data