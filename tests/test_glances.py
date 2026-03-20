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
    _pick_top_gpu,
    _pick_top_network_interface,
    _severity_for_metric,
    _severity_for_ratio,
    _thresholds_for,
    _trend_label,
)


def test_gpu_endpoint_is_registered() -> None:
    endpoints = dict(_ENDPOINTS)
    assert endpoints["gpu"] == "/gpu"


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


def test_trend_label_detects_up_down_and_stable() -> None:
    assert _trend_label([20.0, 21.0, 24.0], epsilon=1.5) == "up"
    assert _trend_label([24.0, 22.0, 20.0], epsilon=1.5) == "down"
    assert _trend_label([20.0, 20.5, 21.0], epsilon=1.5) == "stable"


def test_thresholds_for_uses_plugin_defaults() -> None:
    limits = {
        "mem": {
            "mem_warning": 70.0,
            "mem_critical": 90.0,
        }
    }
    assert _thresholds_for(limits, "mem") == (70.0, 90.0)


def test_thresholds_for_uses_item_specific_cpu_limits() -> None:
    limits = {
        "cpu": {
            "cpu_total_warning": 75.0,
            "cpu_total_critical": 85.0,
        }
    }
    assert _thresholds_for(limits, "cpu", item="total") == (75.0, 85.0)


def test_severity_for_metric_respects_warning_and_critical() -> None:
    assert _severity_for_metric("CPU", 88.0, (75.0, 85.0))[1] == 25
    assert _severity_for_metric("CPU", 76.0, (75.0, 85.0))[1] == 10
    assert _severity_for_metric("CPU", 55.0, (75.0, 85.0))[1] == 0


def test_severity_for_ratio_uses_raw_ratio_scale() -> None:
    assert _severity_for_ratio("Load/core", 6.0, (1.0, 5.0))[1] == 25
    assert _severity_for_ratio("Load/core", 1.2, (1.0, 5.0))[1] == 10
    assert _severity_for_ratio("Load/core", 0.5, (1.0, 5.0))[1] == 0


def test_pick_top_network_interface_skips_loopback_and_prefers_highest_rate() -> None:
    network = [
        {
            "interface_name": "lo",
            "bytes_recv_rate_per_sec": 10_000,
            "bytes_sent_rate_per_sec": 10_000,
            "is_up": True,
        },
        {
            "interface_name": "eth0",
            "bytes_recv_rate_per_sec": 20_000,
            "bytes_sent_rate_per_sec": 30_000,
            "is_up": True,
        },
    ]

    picked = _pick_top_network_interface(network)
    assert picked["interface_name"] == "eth0"


def test_pick_top_gpu_prefers_highest_utilization() -> None:
    gpus = [
        {"name": "GPU-0", "utilization": 12.0},
        {"name": "GPU-1", "utilization": 78.5},
    ]

    picked = _pick_top_gpu(gpus)
    assert picked["name"] == "GPU-1"
