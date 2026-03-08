from __future__ import annotations

import asyncio
import time
from urllib.parse import urlsplit

import httpx

from app.services.glances import (
    _ENDPOINTS,
    _base_url_candidates,
    _fetch_all,
    _get_json_with_fallback,
)


class _FakeResponse:
    def __init__(self, payload: object, delay: float = 0.0, should_fail: bool = False) -> None:
        self._payload = payload
        self._delay = delay
        self._should_fail = should_fail

    async def wait(self) -> _FakeResponse:
        if self._delay > 0:
            await asyncio.sleep(self._delay)
        return self

    def raise_for_status(self) -> None:
        if self._should_fail:
            raise RuntimeError("boom")

    def json(self) -> object:
        return self._payload


class _FakeAsyncClient:
    def __init__(
        self,
        delays: dict[str, float] | None = None,
        failing_hosts: set[str] | None = None,
    ) -> None:
        self._delays = delays
        self._failing_hosts = failing_hosts or set()

    async def get(self, url: str) -> _FakeResponse:
        parsed = urlsplit(url)
        host = parsed.hostname or ""
        if host in self._failing_hosts:
            raise httpx.ConnectError("[Errno -2] Name or service not known")

        path = parsed.path
        if "/api/4" in path:
            path = path.split("/api/4", 1)[1] or "/"
        if not path.startswith("/"):
            path = f"/{path}"

        response = _FakeResponse(
            payload={"endpoint": path, "host": host},
            delay=(self._delays or {}).get(path, 0.0),
            should_fail=False,
        )
        return await response.wait()


def test_fetch_all_waits_for_slowest_endpoint_without_cutting_requests() -> None:
    base_url = "http://glances:61208/api/4"
    slow_path = "/cpu"
    client = _FakeAsyncClient(delays={slow_path: 0.10})

    start = time.monotonic()
    payload = asyncio.run(_fetch_all(client, base_url))
    elapsed = time.monotonic() - start

    assert elapsed >= 0.09
    assert set(payload.keys()) == {key for key, _ in _ENDPOINTS}
    assert "_error" not in payload["cpu"]


def test_base_url_candidates_add_docker_host_fallback_for_glances_service() -> None:
    base_url = "http://glances:61208/api/4"
    assert _base_url_candidates(base_url) == (
        "http://glances:61208/api/4",
        "http://host.docker.internal:61208/api/4",
    )


def test_get_json_with_fallback_retries_on_name_resolution_failure() -> None:
    client = _FakeAsyncClient(failing_hosts={"glances"})
    payload = asyncio.run(_get_json_with_fallback(client, "http://glances:61208/api/4", "/cpu"))

    assert isinstance(payload, dict)
    assert payload.get("endpoint") == "/cpu"
    assert payload.get("host") == "host.docker.internal"


def test_fetch_all_uses_fallback_when_glances_host_cannot_resolve() -> None:
    client = _FakeAsyncClient(failing_hosts={"glances"})
    payload = asyncio.run(_fetch_all(client, "http://glances:61208/api/4"))

    assert set(payload.keys()) == {key for key, _ in _ENDPOINTS}
    assert "_error" not in payload["cpu"]
    assert payload["cpu"]["host"] == "host.docker.internal"
