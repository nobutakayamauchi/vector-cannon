"""Bounded public GET probes. No SDK, credentials, user URLs or redirects."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import time
import urllib.error
import urllib.request


# Mainnet endpoints from the official SDK config/info source at 776438c3.
# Product 116 is BTC_UPSIDE/USD in the captured W11 and current catalogs.
ENDPOINTS = {
    "sdk_docs": "https://sdk.veranta.xyz/",
    "app_status": "https://api.veranta.xyz/v1/app/status",
    "catalog": "https://prod-api.veranta.xyz/data/v2/trading",
    "leaderboard": "https://api.veranta.xyz/v1/history/portfolio/leader-board",
    "recent_btc_upside": "https://api.veranta.xyz/v1/history/recent-trades/116",
}
MAX_BYTES = 2_000_000


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def public_probe(name: str) -> dict:
    if name not in ENDPOINTS:
        raise ValueError("probe not allowed")
    url = ENDPOINTS[name]
    started = datetime.now(timezone.utc).isoformat()
    clock = time.monotonic()
    result = {"url": url, "method": "GET", "requested_at": started, "credentials_sent": False}
    opener = urllib.request.build_opener(NoRedirect())
    request = urllib.request.Request(url, headers={"User-Agent": "w12-s01-read-only-audit"}, method="GET")
    try:
        with opener.open(request, timeout=12) as response:
            body = response.read(MAX_BYTES + 1)
            result.update(http_status=response.status, size_bytes=len(body),
                          body_sha256=hashlib.sha256(body).hexdigest(),
                          outcome="RESPONSE_TOO_LARGE" if len(body) > MAX_BYTES else "READ_ONLY_RESPONSE")
            # Do not interpret aggregate PnL as wallet eligibility or retain
            # response bodies as executable configuration.
    except urllib.error.HTTPError as error:
        outcome = {401: "AUTH_REQUIRED", 403: "ACCESS_DENIED", 404: "SOURCE_UNSUPPORTED",
                   429: "RATE_LIMITED"}.get(error.code, "HTTP_ERROR")
        result.update(http_status=error.code, outcome=outcome)
        error.close()
    except (OSError, urllib.error.URLError, TimeoutError) as error:
        result.update(http_status=None, outcome="NETWORK_UNAVAILABLE", error_type=type(error).__name__)
    result.update(observed_at=datetime.now(timezone.utc).isoformat(),
                  request_latency_ms=round((time.monotonic() - clock) * 1000, 3))
    return result
