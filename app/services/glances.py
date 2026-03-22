"""Async client for the Glances REST API v4.

Fetches the full server state via ``GET /all`` in a single request
and builds a ``ServerSnapshot`` from the aggregated payload.

Individual endpoints are still used for on-demand detail requests
from the ``/glances`` menu.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
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
    """Per-request timeout for Glances API calls."""
    return get_config().glances_request_timeout_seconds


# History endpoints — not included in /all so fetched separately.
_HISTORY_ENDPOINTS: tuple[tuple[str, str], ...] = (
    ("cpu_history", "/cpu/total/history/3"),
    ("mem_history", "/mem/percent/history/3"),
    ("load_history", "/load/min1/history/3"),
)

# On-demand detail endpoints for the /glances interactive menu.
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
    "gpu": "/gpu",
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
    gpu_available: bool
    gpu_count: int
    gpu_name: str
    gpu_util_percent: float
    gpu_mem_percent: float
    gpu_temp_c: float
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
    severity_entries: list[tuple[str, int, str, float, float, bool]] = field(default_factory=list)
    all_network_interfaces: list[dict[str, object]] = field(default_factory=list)
    all_containers: list[dict[str, object]] = field(default_factory=list)
    all_sensors: list[dict[str, object]] = field(default_factory=list)
    all_diskio: list[dict[str, object]] = field(default_factory=list)
    system_info: dict[str, object] = field(default_factory=dict)
    raw_all: dict[str, object] = field(default_factory=dict)

    def as_text(self, locale: str = "en") -> str:
        """Return a compact, operationally-oriented multi-line text representation."""
        from app.utils.i18n import t  # local import to avoid circular deps at module load

        level_icon = {
            "good": "✅",
            "warning": "⚠️",
            "critical": "❌",
        }.get(self.health_level, "ℹ️")

        level_text = t(f"status.level_{self.health_level}", locale=locale)

        _trend_t = {
            "up": t("status.trend_up", locale=locale),
            "down": t("status.trend_down", locale=locale),
            "stable": t("status.trend_stable", locale=locale),
        }

        # Metric name translation for watch_item / findings.
        _metric_t: dict[str, str] = {
            "CPU": t("status.cpu", locale=locale),
            "RAM": t("status.ram", locale=locale),
            "Disk": t("status.disk", locale=locale),
            "Swap": t("status.swap", locale=locale),
            "Load/core": t("status.metric_load_core", locale=locale),
            "GPU": t("status.gpu", locale=locale),
        }

        lines = [
            (
                f"{level_icon} {t('status.health', locale=locale)}: "
                f"{level_text} ({self.health_score}/100)"
            ),
            "",
            (
                f"{t('status.cpu', locale=locale)}: {self.cpu_percent:.1f}% "
                f"({t('status.trend', locale=locale)}: "
                f"{_trend_t.get(self.cpu_trend, self.cpu_trend)})"
            ),
            (
                f"{t('status.ram', locale=locale)}: {self.ram_percent:.1f}% "
                f"({self.ram_used_gb:.1f} / {self.ram_total_gb:.1f} GB, "
                f"{t('status.trend', locale=locale)}: "
                f"{_trend_t.get(self.ram_trend, self.ram_trend)})"
            ),
            (
                f"{t('status.disk', locale=locale)}: {self.disk_percent:.1f}% "
                f"({self.disk_used_gb:.1f} / {self.disk_total_gb:.1f} GB)"
            ),
            (
                f"{t('status.swap', locale=locale)}: {self.swap_percent:.1f}% "
                f"({self.swap_used_gb:.1f} / {self.swap_total_gb:.1f} GB)"
            ),
            (
                f"{t('status.load', locale=locale)}: "
                f"{self.load_1:.2f} / {self.load_5:.2f} / {self.load_15:.2f} "
                f"({t('status.per_core', locale=locale)}: "
                f"{self.load_per_core:.2f}, "
                f"{t('status.trend', locale=locale)}: "
                f"{_trend_t.get(self.load_trend, self.load_trend)})"
            ),
        ]

        if self.gpu_available:
            lines.append(
                f"{t('status.gpu', locale=locale)}: {self.gpu_util_percent:.1f}% "
                f"| VRAM {self.gpu_mem_percent:.1f}% "
                f"| {self.gpu_temp_c:.1f}C ({self.gpu_name})"
            )

        lines.extend(
            [
                (
                    f"{t('status.processes', locale=locale)}: "
                    f"{self.process_running}/{self.process_total} "
                    f"{t('status.running', locale=locale)} | "
                    f"{t('status.network', locale=locale)}: {self.network_top_interface} "
                    f"(RX {self.network_rx_bps / (1024**2):.2f} MB/s, "
                    f"TX {self.network_tx_bps / (1024**2):.2f} MB/s)"
                ),
                f"{t('status.uptime', locale=locale)}: {self.uptime}",
                f"{t('status.docker', locale=locale)}: {self.docker_running} / {self.docker_total}",
            ]
        )

        # Localized key findings from structured severity data.
        if self.severity_entries:
            loc_findings: list[str] = []
            for name, points, level, value, threshold, is_ratio in self.severity_entries:
                if points <= 0:
                    continue
                metric_name = _metric_t.get(name, name)
                if name.startswith("Mount:"):
                    mount_path = name.split(":", 1)[1]
                    msg = t(
                        "status.finding_mount",
                        locale=locale,
                        mount=mount_path,
                        value=f"{value:.1f}",
                    )
                    loc_findings.append(msg)
                elif is_ratio:
                    key = f"status.finding_{level}_ratio"
                    msg = t(
                        key,
                        locale=locale,
                        metric=metric_name,
                        value=f"{value:.2f}",
                        threshold=f"{threshold:.2f}",
                    )
                    loc_findings.append(msg)
                else:
                    key = f"status.finding_{level}_pct"
                    msg = t(
                        key,
                        locale=locale,
                        metric=metric_name,
                        value=f"{value:.1f}",
                        threshold=f"{threshold:.1f}",
                    )
                    loc_findings.append(msg)
            if loc_findings:
                lines.append("")
                lines.append(t("status.key_findings", locale=locale))
                for finding in loc_findings[:3]:
                    lines.append(f"- {finding}")
            else:
                lines.append("")
                lines.append(t("status.key_findings", locale=locale))
                lines.append(f"- {t('status.finding_all_ok', locale=locale)}")
        elif self.key_findings:
            lines.append("")
            lines.append(t("status.key_findings", locale=locale))
            for finding in self.key_findings[:3]:
                lines.append(f"- {finding}")

        lines.append("")
        lines.append(
            f"{t('status.action', locale=locale)}: "
            f"{t(f'status.action_{self.health_level}', locale=locale)}"
        )
        watch_translated = _metric_t.get(self.watch_item, self.watch_item)
        lines.append(f"{t('status.watch_next', locale=locale)}: {watch_translated}")

        if self.top_processes:
            lines.append("")
            lines.append(t("status.top_processes", locale=locale))
            for proc in self.top_processes[:5]:
                name = str(proc.get("name") or "?")
                cpu_pct = _num(proc.get("cpu_percent", 0))
                mem_pct = _num(proc.get("memory_percent", 0))
                lines.append(f"  - {name} | CPU {cpu_pct:.1f}% | RAM {mem_pct:.1f}%")

        if self.top_mounts:
            lines.append("")
            lines.append(t("status.top_mounts", locale=locale))
            for mount in self.top_mounts[:3]:
                mount_point = str(mount.get("mnt_point") or "?")
                mount_percent = _num(mount.get("percent", 0))
                lines.append(f"  - {mount_point}: {mount_percent:.1f}%")

        return "\n".join(lines)

    def as_raw_json(self) -> str:
        """Return aggregated Glances payload serialized as JSON."""
        return json.dumps(self.raw_all, ensure_ascii=True)

    def as_llm_context_json(self) -> str:
        """Return a rich JSON payload with all available server data for LLM reasoning."""

        # --- processes: top 10 with full detail ---
        llm_processes = []
        for proc in self.top_processes[:10]:
            llm_processes.append(
                {
                    "name": proc.get("name") or "?",
                    "pid": proc.get("pid"),
                    "cpu_percent": _round(proc.get("cpu_percent", 0)),
                    "memory_percent": _round(proc.get("memory_percent", 0)),
                    "status": proc.get("status"),
                    "username": proc.get("username"),
                    "num_threads": proc.get("num_threads"),
                }
            )

        # --- all mounts ---
        llm_mounts = []
        for mount in self.top_mounts:
            llm_mounts.append(
                {
                    "mnt_point": mount.get("mnt_point") or "?",
                    "device_name": mount.get("device_name") or "?",
                    "percent": _round(mount.get("percent", 0)),
                    "used_gb": _round(mount.get("used_gb", 0)),
                    "total_gb": _round(mount.get("total_gb", 0)),
                }
            )

        # --- all network interfaces ---
        llm_network = []
        for iface in self.all_network_interfaces:
            name = iface.get("interface_name") or "?"
            if str(name) == "lo":
                continue
            llm_network.append(
                {
                    "interface": name,
                    "is_up": iface.get("is_up"),
                    "speed_mbps": iface.get("speed"),
                    "rx_bps": _round(iface.get("bytes_recv_rate_per_sec", 0)),
                    "tx_bps": _round(iface.get("bytes_sent_rate_per_sec", 0)),
                    "rx_total": iface.get("bytes_recv_gauge"),
                    "tx_total": iface.get("bytes_sent_gauge"),
                }
            )

        # --- all containers ---
        llm_containers = []
        for container in self.all_containers:
            llm_containers.append(
                {
                    "name": container.get("name") or "?",
                    "status": container.get("status"),
                    "cpu_percent": _round(container.get("cpu_percent", 0)),
                    "memory_usage": container.get("memory_usage"),
                    "memory_limit": container.get("memory_limit"),
                    "io_rx": container.get("io_rx"),
                    "io_wx": container.get("io_wx"),
                    "network_rx": container.get("network_rx"),
                    "network_tx": container.get("network_tx"),
                }
            )

        # --- all sensors ---
        llm_sensors = []
        for sensor in self.all_sensors:
            llm_sensors.append(
                {
                    "label": sensor.get("label") or "?",
                    "type": sensor.get("type"),
                    "value": _round(sensor.get("value", 0)),
                    "unit": sensor.get("unit"),
                    "warning": sensor.get("warning"),
                    "critical": sensor.get("critical"),
                }
            )

        # --- disk IO ---
        llm_diskio = []
        for disk in self.all_diskio:
            llm_diskio.append(
                {
                    "disk_name": disk.get("disk_name") or "?",
                    "read_bytes_rate": _round(disk.get("read_bytes_rate_per_sec", 0)),
                    "write_bytes_rate": _round(disk.get("write_bytes_rate_per_sec", 0)),
                    "read_count_rate": _round(disk.get("read_count_rate_per_sec", 0)),
                    "write_count_rate": _round(disk.get("write_count_rate_per_sec", 0)),
                }
            )

        # --- GPU ---
        gpu_info: dict[str, object] = {"available": self.gpu_available}
        if self.gpu_available:
            gpu_info.update(
                {
                    "count": self.gpu_count,
                    "name": self.gpu_name,
                    "util_percent": round(self.gpu_util_percent, 2),
                    "vram_percent": round(self.gpu_mem_percent, 2),
                    "temp_c": round(self.gpu_temp_c, 2),
                }
            )

        payload: dict[str, object] = {
            "health": {
                "score": self.health_score,
                "level": self.health_level,
                "key_findings": self.key_findings[:4],
                "recommended_action": self.recommended_action,
                "watch_next": self.watch_item,
            },
            "cpu": {
                "percent": round(self.cpu_percent, 2),
                "trend": self.cpu_trend,
            },
            "ram": {
                "percent": round(self.ram_percent, 2),
                "used_gb": round(self.ram_used_gb, 2),
                "total_gb": round(self.ram_total_gb, 2),
                "trend": self.ram_trend,
            },
            "swap": {
                "percent": round(self.swap_percent, 2),
                "used_gb": round(self.swap_used_gb, 2),
                "total_gb": round(self.swap_total_gb, 2),
            },
            "load": {
                "min1": round(self.load_1, 3),
                "min5": round(self.load_5, 3),
                "min15": round(self.load_15, 3),
                "per_core": round(self.load_per_core, 3),
                "trend": self.load_trend,
            },
            "storage": {
                "root_percent": round(self.disk_percent, 2),
                "root_used_gb": round(self.disk_used_gb, 2),
                "root_total_gb": round(self.disk_total_gb, 2),
                "all_mounts": llm_mounts,
            },
            "disk_io": llm_diskio,
            "network": {
                "top_interface": self.network_top_interface,
                "all_interfaces": llm_network,
            },
            "processes": {
                "running": self.process_running,
                "total": self.process_total,
                "top": llm_processes,
            },
            "containers": {
                "running": self.docker_running,
                "total": self.docker_total,
                "all": llm_containers,
            },
            "gpu": gpu_info,
            "sensors": llm_sensors,
            "system": self.system_info,
            "uptime": self.uptime,
        }
        return json.dumps(payload, ensure_ascii=True)


async def get_snapshot() -> ServerSnapshot:
    """Fetch Glances metrics via ``/all`` and return a snapshot.

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
    """Fetch the full Glances state via /all and build a ServerSnapshot."""
    cfg = get_config()
    client = await _get_client()
    base_url = cfg.glances_base_url

    # Main payload — single request for all plugins.
    payload = await _get_json_with_fallback(client, base_url, "/all")
    if not isinstance(payload, dict):
        raise RuntimeError("Glances /all response is not a JSON object")

    # Limits — needed for health scoring thresholds.
    try:
        limits_raw = await _get_json_with_fallback(client, base_url, "/all/limits")
    except Exception:
        logger.warning("Could not fetch Glances /all/limits; using defaults")
        limits_raw = {}

    # History endpoints — fetched in parallel (small payloads).
    history_results = await asyncio.gather(
        *(_safe_fetch(client, base_url, path) for _, path in _HISTORY_ENDPOINTS),
        return_exceptions=False,
    )
    history: dict[str, object] = {}
    for (key, _), result in zip(_HISTORY_ENDPOINTS, history_results, strict=False):
        history[key] = result

    if cfg.glances_log_full_payload:
        logger.info("Glances /all payload: %s", json.dumps(payload, ensure_ascii=True))
    else:
        logger.debug("Glances /all payload keys: %s", ", ".join(sorted(payload.keys())))

    return _build_snapshot(payload, _as_dict(limits_raw), history)


async def _safe_fetch(
    client: httpx.AsyncClient,
    base_url: str,
    path: str,
) -> object:
    """Fetch one endpoint, returning empty dict on failure."""
    try:
        return await _get_json_with_fallback(client, base_url, path)
    except Exception:
        logger.debug("Optional endpoint %s failed; skipping", path)
        return {}


def _build_snapshot(
    payload: dict[str, object],
    limits: dict[str, object],
    history: dict[str, object],
) -> ServerSnapshot:
    """Parse an /all payload dict into a ServerSnapshot."""
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
    gpu_payload = payload.get("gpu")
    gpus = _normalize_gpu_list(gpu_payload)
    sensors = _as_list_of_dicts(payload.get("sensors"))
    diskio = _as_list_of_dicts(payload.get("diskio"))
    system = _as_dict(payload.get("system"))

    cpu_history = _history_values(history.get("cpu_history"), "total")
    mem_history = _history_values(history.get("mem_history"), "percent")
    load_history = _history_values(history.get("load_history"), "min1")

    gb = 1024**3

    cpu_percent = _num(cpu.get("total", 0.0))
    ram_percent = _num(mem.get("percent", 0.0))
    swap_percent = _num(memswap.get("percent", 0.0))

    disk_used = _num(disk.get("used", 0))
    disk_size = _num(disk.get("size", 1))
    disk_percent = _num(disk.get("percent", 0.0))

    docker_running = sum(1 for c in containers if c.get("status") == "running")

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
    top_gpu = _pick_top_gpu(gpus)
    gpu_available = bool(top_gpu)
    gpu_name = str(
        top_gpu.get("name")
        or top_gpu.get("gpu_name")
        or top_gpu.get("device_name")
        or top_gpu.get("id")
        or top_gpu.get("gpu_id")
        or "n/a"
    )
    gpu_util_percent = _first_num(
        top_gpu,
        ("utilization", "gpu_utilization", "gpu_percent", "proc", "load", "percent"),
    )
    gpu_mem_percent = _first_num(
        top_gpu,
        (
            "mem",
            "memory_percent",
            "mem_utilization",
            "vram_percent",
            "utilization_memory",
        ),
    )
    if gpu_mem_percent <= 0:
        mem_total = _first_num(top_gpu, ("memory_total", "mem_total", "vram_total"))
        mem_used = _first_num(top_gpu, ("memory_used", "mem_used", "vram_used"))
        if mem_total > 0:
            gpu_mem_percent = min(100.0, (mem_used / mem_total) * 100.0)
    gpu_temp_c = _first_num(top_gpu, ("temperature", "temp", "temperature_gpu"))

    logical_cores = max(_num(core.get("log", load.get("cpucore", 1))), 1.0)
    load_1 = _num(load.get("min1", 0.0))
    load_per_core = load_1 / logical_cores

    top_processes = sorted(
        processes,
        key=lambda p: _num(p.get("cpu_percent", 0)),
        reverse=True,
    )

    # --- System info for LLM context ---
    system_info: dict[str, object] = {}
    for key in ("hostname", "os_name", "os_version", "platform", "linux_distro", "hr_name"):
        val = system.get(key)
        if val is not None:
            system_info[key] = val

    # --- Health scoring ---
    cpu_thresholds = _thresholds_for(limits, "cpu", item="total")
    ram_thresholds = _thresholds_for(limits, "mem")
    disk_thresholds = _thresholds_for(limits, "fs")
    swap_thresholds = _thresholds_for(limits, "memswap")
    load_thresholds = _thresholds_for(limits, "load")
    gpu_thresholds = _thresholds_for(limits, "gpu")

    severity_points: list[tuple[str, int, str, float, float, bool]] = [
        _severity_for_metric("CPU", cpu_percent, cpu_thresholds),
        _severity_for_metric("RAM", ram_percent, ram_thresholds),
        _severity_for_metric("Disk", disk_percent, disk_thresholds),
        _severity_for_metric("Swap", swap_percent, swap_thresholds),
        _severity_for_ratio("Load/core", load_per_core, load_thresholds),
    ]
    if gpu_available:
        severity_points.append(_severity_for_metric("GPU", gpu_util_percent, gpu_thresholds))

    critical_mount = top_mounts[0] if top_mounts else None
    if critical_mount and _num(critical_mount.get("percent", 0)) >= 90:
        mnt_point = str(critical_mount.get("mnt_point") or "?")
        mnt_pct = _num(critical_mount.get("percent", 0))
        severity_points.append((f"Mount:{mnt_point}", 25, "critical", mnt_pct, 90.0, False))

    health_penalty = sum(points for _, points, *_ in severity_points)
    health_score = max(0, min(100, 100 - health_penalty))
    health_level = "good"
    if health_score <= 45:
        health_level = "critical"
    elif health_score <= 75:
        health_level = "warning"

    # English key_findings for LLM context (as_llm_context_json uses these).
    key_findings: list[str] = []
    for name, points, level, value, threshold, is_ratio in severity_points:
        if points <= 0:
            continue
        if name.startswith("Mount:"):
            mount_path = name.split(":", 1)[1]
            key_findings.append(f"Mount {mount_path} at {value:.1f}%")
        elif is_ratio:
            key_findings.append(f"{name} {level} at {value:.2f} (>= {threshold:.2f})")
        else:
            key_findings.append(f"{name} {level} at {value:.1f}% (>= {threshold:.1f}%)")
    key_findings = key_findings[:4]
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
        ("Load/core", load_per_core),
    ]
    if gpu_available:
        watch_candidates.append(("GPU", gpu_util_percent))
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
        gpu_available=gpu_available,
        gpu_count=len(gpus),
        gpu_name=gpu_name,
        gpu_util_percent=gpu_util_percent,
        gpu_mem_percent=gpu_mem_percent,
        gpu_temp_c=gpu_temp_c,
        top_mounts=top_mounts,
        cpu_trend=_trend_label(cpu_history),
        ram_trend=_trend_label(mem_history),
        load_trend=_trend_label(load_history),
        health_score=health_score,
        health_level=health_level,
        key_findings=key_findings,
        recommended_action=recommended_action,
        watch_item=watch_item,
        severity_entries=severity_points,
        top_processes=top_processes[:10],
        all_network_interfaces=network,
        all_containers=containers,
        all_sensors=sensors,
        all_diskio=diskio,
        system_info=system_info,
        raw_all=payload,
    )


def _base_url_candidates(base_url: str) -> tuple[str, ...]:
    primary = _normalize_base_url(base_url)
    parsed = urlsplit(primary)
    if parsed.hostname != "glances":
        return (primary,)

    if parsed.port is None:
        fallback_netloc = "host.docker.internal"
    else:
        fallback_netloc = f"host.docker.internal:{parsed.port}"

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


def _normalize_base_url(base_url: str) -> str:
    """Normalize Glances base URL to include the API v4 prefix."""
    primary = base_url.strip().rstrip("/")
    parsed = urlsplit(primary)

    path = (parsed.path or "").rstrip("/")
    if path in {"", "/"}:
        path = "/api/4"
    elif path.endswith("/api"):
        path = f"{path}/4"
    elif "/api/" not in path:
        path = f"{path}/api/4"

    normalized = urlunsplit(
        (
            parsed.scheme,
            parsed.netloc,
            path,
            parsed.query,
            parsed.fragment,
        )
    )
    return normalized.rstrip("/")


def _safe_url_for_logs(url: str) -> str:
    """Return a log-safe URL without credentials or query parameters."""
    parsed = urlsplit(url)
    host = parsed.hostname or ""
    netloc = host if parsed.port is None else f"{host}:{parsed.port}"
    return urlunsplit((parsed.scheme, netloc, parsed.path, "", ""))


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
        started_at = time.perf_counter()
        try:
            response = await client.get(url)
            elapsed_ms = (time.perf_counter() - started_at) * 1000
            logger.info(
                "Glances request ok endpoint=%s url=%s status=%s elapsed_ms=%.1f",
                path,
                _safe_url_for_logs(url),
                getattr(response, "status_code", "n/a"),
                elapsed_ms,
            )
            response.raise_for_status()
            return response.json()
        except httpx.ConnectError as exc:
            last_error = exc
            elapsed_ms = (time.perf_counter() - started_at) * 1000
            logger.warning(
                "Glances connect error endpoint=%s url=%s elapsed_ms=%.1f error=%r",
                path,
                _safe_url_for_logs(url),
                elapsed_ms,
                exc,
            )
            is_last = index == len(candidates) - 1
            if is_last or not _is_name_resolution_error(exc):
                raise
            logger.warning(
                "Glances host resolution failed for %s (%s); retrying with fallback base URL",
                candidate,
                exc,
            )
        except httpx.TimeoutException as exc:
            last_error = exc
            elapsed_ms = (time.perf_counter() - started_at) * 1000
            logger.warning(
                "Glances timeout endpoint=%s url=%s elapsed_ms=%.1f error=%r",
                path,
                _safe_url_for_logs(url),
                elapsed_ms,
                exc,
            )
            raise
        except httpx.HTTPStatusError as exc:
            last_error = exc
            elapsed_ms = (time.perf_counter() - started_at) * 1000
            status = exc.response.status_code if exc.response is not None else "n/a"
            logger.warning(
                "Glances HTTP error endpoint=%s url=%s status=%s elapsed_ms=%.1f error=%r",
                path,
                _safe_url_for_logs(url),
                status,
                elapsed_ms,
                exc,
            )
            raise
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            elapsed_ms = (time.perf_counter() - started_at) * 1000
            logger.warning(
                "Glances unexpected error endpoint=%s url=%s elapsed_ms=%.1f error=%r",
                path,
                _safe_url_for_logs(url),
                elapsed_ms,
                exc,
            )
            raise

    if last_error is not None:
        raise last_error
    raise RuntimeError("No Glances base URL candidates available")


def _round(value: object) -> float:
    """Round a numeric value to 2 decimal places."""
    return round(_num(value), 2)


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


def _pick_top_gpu(gpu_list: list[dict[str, object]]) -> dict[str, object]:
    if not gpu_list:
        return {}

    return max(
        gpu_list,
        key=lambda gpu: _first_num(
            gpu,
            (
                "utilization",
                "utilization_gpu",
                "gpu_utilization",
                "gpu_percent",
                "proc",
                "load",
                "percent",
            ),
        ),
    )


def _normalize_gpu_list(payload: object) -> list[dict[str, object]]:
    """Normalize Glances GPU payload variants to a flat list of GPU objects."""
    list_payload = _as_list_of_dicts(payload)
    if list_payload:
        return list_payload

    if not isinstance(payload, dict):
        return []

    data = _as_dict(payload)
    for key in ("gpus", "gpu", "devices", "cards"):
        nested = _as_list_of_dicts(data.get(key))
        if nested:
            return nested

    if data:
        return [data]
    return []


def _first_num(source: dict[str, object], keys: tuple[str, ...]) -> float:
    for key in keys:
        if key in source:
            return _num(source.get(key))
    return 0.0


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
) -> tuple[str, int, str, float, float, bool]:
    warning, critical = thresholds
    if value >= critical:
        return name, 25, "critical", value, critical, False
    if value >= warning:
        return name, 10, "high", value, warning, False
    return name, 0, "normal", value, 0.0, False


def _severity_for_ratio(
    name: str,
    value: float,
    thresholds: tuple[float, float],
) -> tuple[str, int, str, float, float, bool]:
    warning, critical = thresholds
    if value >= critical:
        return name, 25, "critical", value, critical, True
    if value >= warning:
        return name, 10, "high", value, warning, True
    return name, 0, "normal", value, 0.0, True


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
