import asyncio
import time

from app.services.glances import _ENDPOINTS, _fetch_all


class _FakeResponse:
    def __init__(self, payload: object, delay: float = 0.0, should_fail: bool = False) -> None:
        self._payload = payload
        self._delay = delay
        self._should_fail = should_fail

    async def wait(self) -> "_FakeResponse":
        if self._delay > 0:
            await asyncio.sleep(self._delay)
        return self

    def raise_for_status(self) -> None:
        if self._should_fail:
            raise RuntimeError("boom")

    def json(self) -> object:
        return self._payload


class _FakeAsyncClient:
    def __init__(self, delays: dict[str, float]) -> None:
        self._delays = delays

    async def get(self, url: str) -> _FakeResponse:
        path = "/" + url.rsplit("/", 1)[-1]
        # Keep the path extraction robust for nested paths like /processlist/top/10.
        if "/processlist/top/10" in url:
            path = "/processlist/top/10"

        response = _FakeResponse(
            payload={"endpoint": path},
            delay=self._delays.get(path, 0.0),
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
