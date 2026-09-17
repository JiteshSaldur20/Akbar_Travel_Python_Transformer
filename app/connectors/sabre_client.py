"""Sabre REST BFM API client (search execution using Java ORBiS authentication).

Latency notes
-------------
* The ``httpx.Client`` is created once and reused, so the TLS session to Sabre
  is kept alive (``Connection: keep-alive``) across searches. Creating a client
  per request forced a fresh DNS lookup + TCP connect + TLS handshake on every
  single search, which alone costs hundreds of milliseconds.
* Callers may pass the already-encoded JSON payload as ``bytes`` to skip a
  redundant encode.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx
import msgspec

from app.connectors.java_auth_client import JavaAuthClient
from app.core.config import Settings, get_settings
from app.core.exceptions import ProviderApiError

logger = logging.getLogger(__name__)


class SabreClient:
    """Client for calling Sabre's Bargain Finder Max API with tokens from Java ORBiS."""

    def __init__(
        self,
        settings: Settings | None = None,
        java_auth_client: JavaAuthClient | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self._transport = transport
        self.java_auth_client = java_auth_client or JavaAuthClient(
            settings=self.settings, transport=transport
        )

        self._http = httpx.Client(
            transport=transport,
            timeout=httpx.Timeout(
                self.settings.sabre_read_timeout_s,
                connect=self.settings.sabre_connect_timeout_s,
            ),
            limits=httpx.Limits(
                max_connections=self.settings.http_max_connections,
                max_keepalive_connections=self.settings.http_max_keepalive_connections,
                keepalive_expiry=self.settings.http_keepalive_expiry_s,
            ),
        )
        logger.info(
            "SabreClient initialized (base_url=%s, connect_timeout=%.1fs, read_timeout=%.1fs)",
            self.settings.sabre_base_url,
            self.settings.sabre_connect_timeout_s,
            self.settings.sabre_read_timeout_s,
        )

    def close(self) -> None:
        """Release the pooled connections held by this client."""
        logger.info("Closing SabreClient pooled connections")
        self._http.close()
        self.java_auth_client.close()

    def _build_search_url(self) -> str:
        if not self.settings.sabre_base_url:
            logger.error("Sabre base URL not configured")
            raise ProviderApiError(
                "Sabre base URL not configured. Please set SABRE_BASE_URL.",
                status_code=503,
            )

        base = self.settings.sabre_base_url.rstrip("/")
        path = self.settings.sabre_search_path.lstrip("/")

        if path.startswith("http://") or path.startswith("https://"):
            url = path
        else:
            url = f"{base}/{path}"

        logger.debug("Built Sabre search URL: %s", url)
        return url

    def search(self, payload: dict[str, Any] | msgspec.Struct | bytes, auth_data=None) -> httpx.Response:
        """Fetch a valid token from Java ORBiS and execute a BFM search against Sabre."""
        t0 = time.perf_counter()

        logger.info("Fetching Sabre token from Java ORBiS...")
        if auth_data is None:
            auth_data = self.java_auth_client.get_token("SABRE", "sabre_ndc")
        t_token = time.perf_counter()
        logger.info("Sabre token acquired in %.0f ms (expires=%s, pcc=%s)", (t_token - t0) * 1000, auth_data.expiresAt, auth_data.pcc)

        token_type = auth_data.tokenType or "Bearer"
        token = auth_data.accessToken

        search_url = self._build_search_url()

        if isinstance(payload, (bytes, bytearray)):
            encoded_payload = bytes(payload)
        else:
            encoded_payload = msgspec.json.encode(payload)

        logger.info("Dispatching BFM request to %s (%d bytes)", search_url, len(encoded_payload))

        headers = {
            "Authorization": f"{token_type} {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        try:
            resp = self._http.post(
                search_url,
                content=encoded_payload,
                headers=headers,
            )
            t_resp = time.perf_counter()
            logger.info(
                "Sabre BFM response: status=%d, size=%d bytes, latency=%.0f ms",
                resp.status_code,
                len(resp.content),
                (t_resp - t_token) * 1000,
            )
            if resp.status_code >= 400:
                logger.warning("Sabre BFM error response: status=%d", resp.status_code)
                if logger.isEnabledFor(logging.DEBUG):
                    logger.debug("Sabre error body: %s", resp.text[:2000])
            return resp
        except httpx.HTTPError as exc:
            logger.error("Sabre BFM request failed: %s", exc)
            raise ProviderApiError(
                f"Failed to connect to Sabre BFM endpoint at {search_url}: {exc}",
                status_code=502,
            ) from exc

    def get_auth_data(self) -> "JavaAuthClient":
        """Fetch auth data (including PCC) from Java ORBiS without making a search call."""
        from app.connectors.java_auth_client import JavaProviderAuthResponse
        return self.java_auth_client.get_token("SABRE", "sabre_ndc")