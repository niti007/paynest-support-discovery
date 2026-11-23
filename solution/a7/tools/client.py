"""HTTP client for the payroll API (mock or real). Timeout, bearer auth, tenant header, 429 back-off."""
from __future__ import annotations

import os
import time

import httpx

DEFAULT_URL = "http://localhost:18081"
DEFAULT_TOKEN = "a7-mock-token-dev"


class ApiError(Exception):
    def __init__(self, status: int, error: str, retry_after: int | None = None):
        super().__init__(f"{status} {error}")
        self.status, self.error, self.retry_after = status, error, retry_after


class PayrollClient:
    """max_wait: longest Retry-After we are willing to sleep for; longer means give up (ApiError 429)."""

    def __init__(self, base_url: str | None = None, token: str | None = None, *, timeout: float = 10.0,
                 retries: int = 2, max_wait: float = 10.0, sleep=time.sleep,
                 transport: httpx.BaseTransport | None = None):
        self.base_url = base_url or os.environ.get("A7_PAYROLL_API_URL", DEFAULT_URL)
        self.token = token or os.environ.get("A7_PAYROLL_API_TOKEN", DEFAULT_TOKEN)
        self.retries, self.max_wait, self.sleep = retries, max_wait, sleep
        self.http = httpx.Client(base_url=self.base_url, timeout=timeout, transport=transport)

    def get(self, path: str, tenant_id: str, params: dict | None = None) -> dict:
        headers = {"Authorization": f"Bearer {self.token}", "X-Tenant-ID": tenant_id}
        for attempt in range(self.retries + 1):
            try:
                r = self.http.get(path, params=params, headers=headers)
            except httpx.HTTPError as e:
                raise ApiError(503, f"unreachable: {type(e).__name__}") from None
            if r.status_code == 429:
                wait = int(r.headers.get("Retry-After", "1") or 1)
                if attempt >= self.retries or wait > self.max_wait:
                    raise ApiError(429, "rate_limited", retry_after=wait)
                self.sleep(wait)
                continue
            if r.status_code >= 400:
                try:
                    msg = r.json().get("error", "error")
                except ValueError:
                    msg = "error"
                raise ApiError(r.status_code, msg)
            return r.json()
        raise ApiError(429, "rate_limited")  # pragma: no cover


_client: PayrollClient | None = None


def get_client() -> PayrollClient:
    global _client
    if _client is None:
        _client = PayrollClient()
    return _client


def set_client(c: PayrollClient | None) -> None:
    """Tests (and Builder A's wiring) can inject a client."""
    global _client
    _client = c
