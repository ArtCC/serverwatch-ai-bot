from __future__ import annotations

import asyncio
from urllib.parse import urlsplit

import httpx

from app.services.glances import (
    _HISTORY_ENDPOINTS,
    _base_url_candidates,
    _build_snapshot,
    _get_json_with_fallback,
    _normalize_base_url,
    _normalize_gpu_list,
    _num,
    _pick_top_gpu,
    _pick_top_network_interface,
    _round,
    _severity_for_metric,
    _severity_for_ratio,
    _thresholds_for,
    _trend_label,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _minimal_all_payload() -> dict[str, object]:
    """Return a minimal /all-style payload for building a snapshot."""
    return {
        "cpu": {"total": 25.0},
        "mem": {"percent": 40.0, "used": 4 * 1024**3, "total": 16 * 1024**3},
        "memswap": {"percent": 5.0, "used": 0, "total": 2 * 1024**3},
        "fs": [{"mnt_point": "/", "percent": 55.0, "used": 50 * 1024**3, "size": 100 * 1024**3}],
        "load": {"min1": 1.5, "min5": 1.2, "min15": 1.0, "cpucore": 4},
        "core": {"log": 4},
        "processcount": {"total": 200, "running": 3},
        "uptime": "3 days",
        "containers": [{"name": "bot", "status": "running"}],
        "processlist": [
            {"name": "python", "cpu_percent": 12.0, "memory_percent": 3.5, "pid": 1},
        ],
        "network": [
            {
                "interface_name": "eth0",
                "bytes_recv_rate_per_sec": 1000,
                "bytes_sent_rate_per_sec": 500,
                "is_up": True,
            }
        ],
        "gpu": [],
        "sensors": [{"label": "Core 0", "value": 55, "unit": "C", "type": "temperature_core"}],
        "diskio": [
            {
                "disk_name": "sda",
                "read_bytes_rate_per_sec": 100,
                "write_bytes_rate_per_sec": 200,
            }
        ],
        "system": {"hostname": "myhost", "os_name": "Linux"},
    }


class _FakeResponse:
    def __init__(self, payload: object, should_fail: bool = False) -> None:
        self._payload = payload
        self._should_fail = should_fail

    def raise_for_status(self) -> None:
        if self._should_fail:
            raise RuntimeError("boom")

    def json(self) -> object:
        return self._payload


class _FakeAsyncClient:
    def __init__(
        self,
        failing_hosts: set[str] | None = None,
    ) -> None:
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

        return _FakeResponse(
            payload={"endpoint": path, "host": host},
            should_fail=False,
        )


# ---------------------------------------------------------------------------
# Tests: build_snapshot
# ---------------------------------------------------------------------------


def test_build_snapshot_from_all_payload() -> None:
    payload = _minimal_all_payload()
    snap = _build_snapshot(payload, {}, {})
    assert snap.cpu_percent == 25.0
    assert snap.ram_percent == 40.0
    assert snap.docker_running == 1
    assert snap.docker_total == 1
    assert snap.uptime == "3 days"
    assert len(snap.all_sensors) == 1
    assert snap.system_info.get("hostname") == "myhost"


def test_build_snapshot_includes_all_mounts() -> None:
    payload = _minimal_all_payload()
    payload["fs"] = [
        {"mnt_point": "/", "percent": 55.0, "used": 50 * 1024**3, "size": 100 * 1024**3},
        {"mnt_point": "/data", "percent": 80.0, "used": 80 * 1024**3, "size": 100 * 1024**3},
        {"mnt_point": "/boot", "percent": 30.0, "used": 1 * 1024**3, "size": 5 * 1024**3},
    ]
    snap = _build_snapshot(payload, {}, {})
    assert len(snap.top_mounts) == 3
    assert snap.top_mounts[0]["mnt_point"] == "/data"


def test_build_snapshot_enriched_llm_context() -> None:
    import json

    payload = _minimal_all_payload()
    snap = _build_snapshot(payload, {}, {})
    ctx = json.loads(snap.as_llm_context_json())

    assert "cpu" in ctx
    assert "ram" in ctx
    assert "storage" in ctx
    assert "disk_io" in ctx
    assert "sensors" in ctx
    assert "system" in ctx
    assert "containers" in ctx
    assert ctx["containers"]["running"] == 1
    assert len(ctx["containers"]["all"]) == 1
    assert ctx["system"].get("hostname") == "myhost"
    assert len(ctx["sensors"]) == 1


def test_base_url_candidates_add_docker_host_fallback_for_glances_service() -> None:
    base_url = "http://glances:61208/api/4"
    assert _base_url_candidates(base_url) == (
        "http://glances:61208/api/4",
        "http://host.docker.internal:61208/api/4",
    )


def test_normalize_base_url_adds_api_prefix_when_missing() -> None:
    assert _normalize_base_url("http://192.168.1.20:61208") == "http://192.168.1.20:61208/api/4"


def test_normalize_base_url_expands_api_root_without_version() -> None:
    assert _normalize_base_url("http://glances:61208/api") == "http://glances:61208/api/4"


def test_get_json_with_fallback_retries_on_name_resolution_failure() -> None:
    client = _FakeAsyncClient(failing_hosts={"glances"})
    payload = asyncio.run(_get_json_with_fallback(client, "http://glances:61208/api/4", "/cpu"))

    assert isinstance(payload, dict)
    assert payload.get("endpoint") == "/cpu"
    assert payload.get("host") == "host.docker.internal"


def test_get_json_with_fallback_accepts_base_url_without_api_path() -> None:
    client = _FakeAsyncClient()
    payload = asyncio.run(_get_json_with_fallback(client, "http://192.168.1.20:61208", "/cpu"))

    assert isinstance(payload, dict)
    assert payload.get("endpoint") == "/cpu"
    assert payload.get("host") == "192.168.1.20"


# ---------------------------------------------------------------------------
# Tests: history endpoints constant
# ---------------------------------------------------------------------------


def test_history_endpoints_are_defined() -> None:
    keys = {key for key, _ in _HISTORY_ENDPOINTS}
    assert "cpu_history" in keys
    assert "mem_history" in keys
    assert "load_history" in keys


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


def test_normalize_gpu_list_accepts_nested_payload_shape() -> None:
    payload = {
        "gpus": [
            {"name": "Intel UHD", "utilization_gpu": 23.0},
            {"name": "RTX 4070", "utilization_gpu": 61.0},
        ]
    }

    normalized = _normalize_gpu_list(payload)
    assert len(normalized) == 2
    assert normalized[1]["name"] == "RTX 4070"


def test_gpu_mem_percent_can_be_derived_from_used_total_fields() -> None:
    gpu = {
        "name": "RTX 4070",
        "memory_used": 4096,
        "memory_total": 12288,
    }

    used = _num(gpu.get("memory_used"))
    total = _num(gpu.get("memory_total"))
    percent = (used / total) * 100.0

    assert round(percent, 1) == 33.3


def test_round_returns_two_decimal_places() -> None:
    assert _round(3.14159) == 3.14
    assert _round("2.999") == 3.0
    assert _round(None) == 0.0
