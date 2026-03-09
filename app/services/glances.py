"""Async client for the Glances REST API v4.

Only the endpoints needed for the bot are implemented.
All functions raise httpx.HTTPError on connectivity / HTTP errors.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

import httpx

from app.core.config import get_config

logger = logging.getLogger("serverwatch")

_SNAPSHOT_TTL = 10.0  # seconds — reuse a recent snapshot within this window
_cached_snapshot: ServerSnapshot | None = None
_cache_timestamp: float = 0.0
_cache_lock = asyncio.Lock()
_client: httpx.AsyncClient | None = None
_client_timeout: float | None = None
_client_lock = asyncio.Lock()


def _request_timeout() -> float:
    """Per-request timeout for individual Glances endpoints."""
    return get_config().glances_request_timeout_seconds


_ENDPOINTS: tuple[tuple[str, str], ...] = (
    ("status", "/status"),
    ("cpu", "/cpu"),
    ("load", "/load"),
    ("mem", "/mem"),
    ("memswap", "/memswap"),
    ("fs", "/fs"),
    ("processcount", "/processcount"),
    ("uptime", "/uptime"),
    ("diskio", "/diskio"),
    ("network", "/network"),
    ("containers", "/containers"),
    ("processlist", "/processlist/top/10"),
    ("smart", "/smart"),
    ("sensors", "/sensors"),
    ("system", "/system"),
    ("core", "/core"),
    ("version", "/version"),
    ("pluginslist", "/pluginslist"),
    ("limits", "/all/limits"),
    ("cpu_history", "/cpu/total/history/3"),
    ("mem_history", "/mem/percent/history/3"),
    ("load_history", "/load/min1/history/3"),
)

_DETAIL_ENDPOINTS: dict[str, str] = {
    "cpu": "/cpu",
    "mem": "/mem",
    "fs": "/fs",
    "load": "/load",
    "network": "/network",
    "containers": "/containers",
    "processlist": "/processlist/top/10",
    "uptime": "/uptime",
    "system": "/system",
    "sensors": "/sensors",
}


@dataclass
class ServerSnapshot:
    """Normalised view of the most relevant server metrics."""

    cpu_percent: float
    ram_percent: float
    ram_used_gb: float
    ram_total_gb: float
    disk_percent: float
    disk_used_gb: float
    disk_total_gb: float
    swap_percent: float
    swap_used_gb: float
    swap_total_gb: float
    load_1: float
    load_5: float
    load_15: float
    load_per_core: float
    uptime: str
    docker_running: int
    docker_total: int
    process_total: int
    process_running: int
    network_top_interface: str
    network_rx_bps: float
    network_tx_bps: float
    top_mounts: list[dict[str, object]]
    cpu_trend: str
    ram_trend: str
    load_trend: str
    health_score: int
    health_level: str
    key_findings: list[str]
    recommended_action: str
    watch_item: str
    top_processes: list[dict[str, object]]
    raw_all: dict[str, object]

    def as_text(self, locale: str = "en") -> str:
        """Return a compact, operationally-oriented multi-line text representation."""
        from app.utils.i18n import t  # local import to avoid circular deps at module load

        level_icon = {
            "good": "✅",
            "warning": "⚠️",
            "critical": "❌",
        }.get(self.health_level, "ℹ️")

        lines = [
            f"{level_icon} Health: {self.health_level.upper()} ({self.health_score}/100)",
            "",
            f"{t('status.cpu', locale=locale)}: {self.cpu_percent:.1f}% (trend: {self.cpu_trend})",
            (
                f"{t('status.ram', locale=locale)}: {self.ram_percent:.1f}% "
                f"({self.ram_used_gb:.1f} / {self.ram_total_gb:.1f} GB, trend: {self.ram_trend})"
            ),
            (
                f"{t('status.disk', locale=locale)}: {self.disk_percent:.1f}% "
                f"({self.disk_used_gb:.1f} / {self.disk_total_gb:.1f} GB)"
            ),
            (
                f"Swap: {self.swap_percent:.1f}% "
                f"({self.swap_used_gb:.1f} / {self.swap_total_gb:.1f} GB)"
            ),
            (
                f"{t('status.load', locale=locale)}: "
                f"{self.load_1:.2f} / {self.load_5:.2f} / {self.load_15:.2f} "
                f"(per-core: {self.load_per_core:.2f}, trend: {self.load_trend})"
            ),
            (
                f"Processes: {self.process_running}/{self.process_total} running | "
                f"Net: {self.network_top_interface} "
                f"(RX {self.network_rx_bps / (1024**2):.2f} MB/s, "
                f"TX {self.network_tx_bps / (1024**2):.2f} MB/s)"
            ),
            f"{t('status.uptime', locale=locale)}: {self.uptime}",
            f"{t('status.docker', locale=locale)}: {self.docker_running} / {self.docker_total}",
        ]

        if self.key_findings:
            lines.append("")
            lines.append("Key findings")
            for finding in self.key_findings[:3]:
                lines.append(f"- {finding}")

        lines.append("")
        lines.append(f"Action: {self.recommended_action}")
        lines.append(f"Watch next: {self.watch_item}")

        if self.top_processes:
            lines.append("")
            lines.append("Top processes (CPU)")
            for proc in self.top_processes[:5]:
                name = proc.get("name") or "?"
                cpu_pct = _num(proc.get("cpu_percent", 0))
                mem_pct = _num(proc.get("memory_percent", 0))
                lines.append(f"  - {name} | CPU {cpu_pct:.1f}% | RAM {mem_pct:.1f}%")

        if self.top_mounts:
            lines.append("")
            lines.append("Top mounts")
            for mount in self.top_mounts[:3]:
                mount_point = str(mount.get("mnt_point") or "?")
                mount_percent = _num(mount.get("percent", 0))
                lines.append(f"  - {mount_point}: {mount_percent:.1f}%")

        return "\n".join(lines)

    def as_raw_json(self) -> str:
        """Return aggregated Glances payload serialized as JSON."""
        return json.dumps(self.raw_all, ensure_ascii=True)

    def as_llm_context_json(self) -> str:
        """Return a compact JSON payload optimized for LLM reasoning latency."""
        payload: dict[str, object] = {
            "health": {
                "score": self.health_score,
                "level": self.health_level,
                "key_findings": self.key_findings[:4],
                "recommended_action": self.recommended_action,
                "watch_next": self.watch_item,
            },
            "core": {
                "cpu_percent": round(self.cpu_percent, 2),
                "ram_percent": round(self.ram_percent, 2),
                "disk_percent": round(self.disk_percent, 2),
                "swap_percent": round(self.swap_percent, 2),
                "load": {
                    "min1": round(self.load_1, 3),
                    "min5": round(self.load_5, 3),
                    "min15": round(self.load_15, 3),
                    "per_core": round(self.load_per_core, 3),
                },
                "trends": {
                    "cpu": self.cpu_trend,
                    "ram": self.ram_trend,
                    "load": self.load_trend,
                },
            },
            "processes": {
                "running": self.process_running,
                "total": self.process_total,
                "top_cpu": self.top_processes[:5],
            },
            "storage": {
                "root_percent": round(self.disk_percent, 2),
                "top_mounts": self.top_mounts[:3],
            },
            "network": {
                "interface": self.network_top_interface,
                "rx_bps": round(self.network_rx_bps, 2),
                "tx_bps": round(self.network_tx_bps, 2),
            },
            "containers": {
                "running": self.docker_running,
                "total": self.docker_total,
            },
            "uptime": self.uptime,
        }
        return json.dumps(payload, ensure_ascii=True)


async def get_snapshot() -> ServerSnapshot:
    """Fetch Glances metrics from individual endpoints and return a snapshot.

    Results are cached for _SNAPSHOT_TTL seconds to avoid redundant HTTP
    calls when multiple parts of the bot query metrics in quick succession.
    """
    global _cached_snapshot, _cache_timestamp
    now = time.monotonic()
    if _cached_snapshot is not None and (now - _cache_timestamp) < _SNAPSHOT_TTL:
        logger.debug("Returning cached Glances snapshot (age=%.1fs)", now - _cache_timestamp)
        return _cached_snapshot

    async with _cache_lock:
        now = time.monotonic()
        if _cached_snapshot is not None and (now - _cache_timestamp) < _SNAPSHOT_TTL:
            logger.debug("Returning cached Glances snapshot (age=%.1fs)", now - _cache_timestamp)
            return _cached_snapshot

        snapshot = await _fetch_snapshot()
        _cached_snapshot = snapshot
        _cache_timestamp = time.monotonic()
        return snapshot


async def _get_client() -> httpx.AsyncClient:
    global _client, _client_timeout
    timeout = _request_timeout()

    if _client is not None and _client_timeout == timeout:
        return _client

    async with _client_lock:
        if _client is not None and _client_timeout == timeout:
            return _client

        if _client is not None:
            await _client.aclose()

        _client = httpx.AsyncClient(timeout=timeout)
        _client_timeout = timeout
        return _client


async def close_client() -> None:
    """Close the shared Glances HTTP client when the app shuts down."""
    global _client, _client_timeout
    async with _client_lock:
        if _client is None:
            return
        await _client.aclose()
        _client = None
        _client_timeout = None


def detail_endpoint_keys() -> tuple[str, ...]:
    """Return supported endpoint keys for live detail requests."""
    return tuple(_DETAIL_ENDPOINTS.keys())


async def get_live_endpoint_detail(key: str) -> object:
    """Fetch one Glances endpoint on demand without using snapshot cache."""
    path = _DETAIL_ENDPOINTS.get(key)
    if path is None:
        raise ValueError(f"Unsupported Glances detail endpoint: {key}")

    cfg = get_config()
    client = await _get_client()
    return await _get_json_with_fallback(client, cfg.glances_base_url, path)


async def _fetch_snapshot() -> ServerSnapshot:
    """Internal: always performs live HTTP requests to Glances."""
    cfg = get_config()

    client = await _get_client()
    payload = await _fetch_all(client, cfg.glances_base_url)

    if cfg.glances_log_full_payload:
        logger.info("Glances aggregated payload: %s", json.dumps(payload, ensure_ascii=True))
    else:
        logger.debug("Glances aggregated payload keys: %s", ", ".join(sorted(payload.keys())))

    cpu = _as_dict(payload.get("cpu"))
    mem = _as_dict(payload.get("mem"))
    memswap = _as_dict(payload.get("memswap"))
    fs_data = _as_list_of_dicts(payload.get("fs"))
    disk = _pick_root_fs(fs_data)
    load = _as_dict(payload.get("load"))
    core = _as_dict(payload.get("core"))
    processcount = _as_dict(payload.get("processcount"))
    uptime = payload.get("uptime")
    containers = _as_list_of_dicts(payload.get("containers"))
    processes = _as_list_of_dicts(payload.get("processlist"))
    network = _as_list_of_dicts(payload.get("network"))
    limits = _as_dict(payload.get("limits"))

    cpu_history = _history_values(payload.get("cpu_history"), "total")
    mem_history = _history_values(payload.get("mem_history"), "percent")
    load_history = _history_values(payload.get("load_history"), "min1")

    gb = 1024**3

    cpu_percent = _num(cpu.get("total", 0.0))
    ram_percent = _num(mem.get("percent", 0.0))
    swap_percent = _num(memswap.get("percent", 0.0))

    disk_used = _num(disk.get("used", 0))
    disk_size = _num(disk.get("size", 1))
    disk_percent = _num(disk.get("percent", 0.0))

    docker_running = sum(1 for container in containers if container.get("status") == "running")

    top_mounts = sorted(
        [
            {
                "mnt_point": mount.get("mnt_point") or mount.get("device_name") or "?",
                "device_name": mount.get("device_name") or "?",
                "percent": _num(mount.get("percent", 0)),
                "used_gb": _num(mount.get("used", 0)) / gb,
                "total_gb": max(_num(mount.get("size", 0)), 1.0) / gb,
            }
            for mount in fs_data
        ],
        key=lambda mount: _num(mount.get("percent", 0)),
        reverse=True,
    )

    top_iface = _pick_top_network_interface(network)

    logical_cores = max(_num(core.get("log", load.get("cpucore", 1))), 1.0)
    load_1 = _num(load.get("min1", 0.0))
    load_per_core = load_1 / logical_cores

    top_processes = sorted(
        processes,
        key=lambda proc: _num(proc.get("cpu_percent", 0)),
        reverse=True,
    )

    cpu_thresholds = _thresholds_for(limits, "cpu", item="total")
    ram_thresholds = _thresholds_for(limits, "mem")
    disk_thresholds = _thresholds_for(limits, "fs")
    swap_thresholds = _thresholds_for(limits, "memswap")
    load_thresholds = _thresholds_for(limits, "load")

    severity_points: list[tuple[str, int, str]] = [
        _severity_for_metric("CPU", cpu_percent, cpu_thresholds),
        _severity_for_metric("RAM", ram_percent, ram_thresholds),
        _severity_for_metric("Disk", disk_percent, disk_thresholds),
        _severity_for_metric("Swap", swap_percent, swap_thresholds),
        _severity_for_metric("Load/core", load_per_core * 100.0, load_thresholds),
    ]

    critical_mount = top_mounts[0] if top_mounts else None
    if critical_mount and _num(critical_mount.get("percent", 0)) >= 90:
        severity_points.append(
            (
                "Mount",
                25,
                (
                    f"Mount {critical_mount.get('mnt_point', '?')} "
                    f"at {_num(critical_mount.get('percent', 0)):.1f}%"
                ),
            )
        )

    health_penalty = sum(points for _, points, _ in severity_points)
    health_score = max(0, min(100, 100 - health_penalty))
    health_level = "good"
    if health_score <= 45:
        health_level = "critical"
    elif health_score <= 75:
        health_level = "warning"

    key_findings = [detail for _, points, detail in severity_points if points > 0][:4]
    if not key_findings:
        key_findings = ["All primary metrics are within expected range"]

    recommended_action = "No immediate action needed"
    if health_level == "warning":
        recommended_action = "Review top process and watch trends for the next 5 minutes"
    elif health_level == "critical":
        recommended_action = "Mitigate the hottest resource now and verify service latency"

    watch_candidates = [
        ("CPU", cpu_percent),
        ("RAM", ram_percent),
        ("Disk", disk_percent),
        ("Swap", swap_percent),
        ("Load/core", load_per_core * 100.0),
    ]
    watch_item = max(watch_candidates, key=lambda item: item[1])[0]

    return ServerSnapshot(
        cpu_percent=cpu_percent,
        ram_percent=ram_percent,
        ram_used_gb=_num(mem.get("used", 0)) / gb,
        ram_total_gb=max(_num(mem.get("total", 0)), 1.0) / gb,
        disk_percent=float(disk_percent),
        disk_used_gb=disk_used / gb,
        disk_total_gb=disk_size / gb,
        swap_percent=swap_percent,
        swap_used_gb=_num(memswap.get("used", 0)) / gb,
        swap_total_gb=max(_num(memswap.get("total", 0)), 1.0) / gb,
        load_1=load_1,
        load_5=_num(load.get("min5", 0.0)),
        load_15=_num(load.get("min15", 0.0)),
        load_per_core=load_per_core,
        uptime=str(uptime).strip('"') if uptime else "n/a",
        docker_running=docker_running,
        docker_total=len(containers),
        process_total=int(_num(processcount.get("total", 0))),
        process_running=int(_num(processcount.get("running", 0))),
        network_top_interface=str(top_iface.get("interface_name") or "n/a"),
        network_rx_bps=_num(top_iface.get("bytes_recv_rate_per_sec", 0)),
        network_tx_bps=_num(top_iface.get("bytes_sent_rate_per_sec", 0)),
        top_mounts=top_mounts[:3],
        cpu_trend=_trend_label(cpu_history),
        ram_trend=_trend_label(mem_history),
        load_trend=_trend_label(load_history),
        health_score=health_score,
        health_level=health_level,
        key_findings=key_findings,
        recommended_action=recommended_action,
        watch_item=watch_item,
        top_processes=top_processes[:5],
        raw_all=payload,
    )


async def _fetch_all(client: httpx.AsyncClient, base_url: str) -> dict[str, object]:
    """Fetch all Glances endpoints concurrently and aggregate responses.

    The flow intentionally waits for every endpoint request to complete (or fail
    by per-request timeout) before returning. This avoids cutting pending calls
    mid-flight and guarantees a complete best-effort bundle for the LLM context.
    """

    async def _fetch_endpoint(key: str, path: str) -> tuple[str, object]:
        try:
            payload = await _get_json_with_fallback(client, base_url, path)
            return key, payload
        except Exception as exc:  # noqa: BLE001
            return key, {"_error": str(exc), "_endpoint": path}

    results = await asyncio.gather(*(_fetch_endpoint(key, path) for key, path in _ENDPOINTS))
    return {key: value for key, value in results}


def _base_url_candidates(base_url: str) -> tuple[str, ...]:
    primary = base_url.rstrip("/")
    parsed = urlsplit(primary)
    if parsed.hostname != "glances":
        return (primary,)

    if parsed.port is None:
        fallback_netloc = "host.docker.internal"
    else:
        fallback_netloc = f"host.docker.internal:{parsed.port}"

    # Preserve credentials if the user configured them.
    if parsed.username:
        userinfo = parsed.username
        if parsed.password:
            userinfo = f"{userinfo}:{parsed.password}"
        fallback_netloc = f"{userinfo}@{fallback_netloc}"

    fallback = urlunsplit(
        (
            parsed.scheme,
            fallback_netloc,
            parsed.path,
            parsed.query,
            parsed.fragment,
        )
    ).rstrip("/")
    if fallback == primary:
        return (primary,)
    return (primary, fallback)


def _is_name_resolution_error(exc: httpx.ConnectError) -> bool:
    text = str(exc).lower()
    return "name or service not known" in text or "nodename nor servname provided" in text


async def _get_json_with_fallback(
    client: httpx.AsyncClient,
    base_url: str,
    path: str,
) -> object:
    candidates = _base_url_candidates(base_url)
    last_error: Exception | None = None

    for index, candidate in enumerate(candidates):
        url = f"{candidate}{path}"
        try:
            response = await client.get(url)
            response.raise_for_status()
            return response.json()
        except httpx.ConnectError as exc:
            last_error = exc
            is_last = index == len(candidates) - 1
            if is_last or not _is_name_resolution_error(exc):
                raise
            logger.warning(
                "Glances host resolution failed for %s (%s); retrying with fallback base URL",
                candidate,
                exc,
            )
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            raise

    if last_error is not None:
        raise last_error
    raise RuntimeError("No Glances base URL candidates available")


def _num(value: object) -> float:
    """Safely cast an API value to float."""
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0


def _as_dict(value: object) -> dict[str, object]:
    """Return a JSON object with string keys, or an empty dict."""
    if not isinstance(value, dict):
        return {}
    return {str(k): v for k, v in value.items()}


def _as_list_of_dicts(value: object) -> list[dict[str, object]]:
    """Return a list containing only JSON objects with string keys."""
    if not isinstance(value, list):
        return []
    result: list[dict[str, object]] = []
    for item in value:
        if isinstance(item, dict):
            result.append({str(k): v for k, v in item.items()})
    return result


def _pick_root_fs(fs_list: list[dict[str, object]]) -> dict[str, object]:
    """Return the root filesystem entry, or the largest one as fallback."""
    if not fs_list:
        return {}
    for entry in fs_list:
        if entry.get("mnt_point") == "/":
            return entry
    return max(fs_list, key=lambda entry: _num(entry.get("size", 0)))


def _pick_top_network_interface(network_list: list[dict[str, object]]) -> dict[str, object]:
    if not network_list:
        return {}

    filtered = [
        iface
        for iface in network_list
        if bool(iface.get("is_up", True)) and str(iface.get("interface_name") or "") != "lo"
    ]
    if not filtered:
        filtered = network_list

    return max(
        filtered,
        key=lambda iface: (
            _num(iface.get("bytes_recv_rate_per_sec", 0))
            + _num(iface.get("bytes_sent_rate_per_sec", 0))
        ),
    )


def _thresholds_for(
    limits: dict[str, object],
    plugin: str,
    *,
    item: str | None = None,
) -> tuple[float, float]:
    plugin_limits = _as_dict(limits.get(plugin))

    if item:
        warning = _num(plugin_limits.get(f"{plugin}_{item}_warning", 75.0))
        critical = _num(plugin_limits.get(f"{plugin}_{item}_critical", 90.0))
    else:
        warning = _num(plugin_limits.get(f"{plugin}_warning", 75.0))
        critical = _num(plugin_limits.get(f"{plugin}_critical", 90.0))

    warning = max(0.0, warning)
    critical = max(warning, critical)
    return warning, critical


def _severity_for_metric(
    name: str,
    value: float,
    thresholds: tuple[float, float],
) -> tuple[str, int, str]:
    warning, critical = thresholds
    if value >= critical:
        return name, 25, f"{name} critical at {value:.1f}% (>= {critical:.1f}%)"
    if value >= warning:
        return name, 10, f"{name} high at {value:.1f}% (>= {warning:.1f}%)"
    return name, 0, f"{name} normal"


def _history_values(payload: object, key: str) -> list[float]:
    data = _as_dict(payload)
    values = data.get(key)
    if not isinstance(values, list):
        return []

    parsed: list[float] = []
    for item in values:
        if not isinstance(item, list) or len(item) != 2:
            continue
        parsed.append(_num(item[1]))
    return parsed


def _trend_label(values: list[float], epsilon: float = 1.5) -> str:
    if len(values) < 2:
        return "stable"
    delta = values[-1] - values[0]
    if delta > epsilon:
        return "up"
    if delta < -epsilon:
        return "down"
    return "stable"
