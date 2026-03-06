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

import httpx

from app.core.config import get_config

logger = logging.getLogger("serverwatch")

_SNAPSHOT_TTL = 10.0  # seconds — reuse a recent snapshot within this window
_cached_snapshot: ServerSnapshot | None = None
_cache_timestamp: float = 0.0


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
    ("sensors", "/sensors"),
    ("system", "/system"),
    ("core", "/core"),
    ("version", "/version"),
    ("pluginslist", "/pluginslist"),
)


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
    load_1: float
    load_5: float
    load_15: float
    uptime: str
    docker_running: int
    docker_total: int
    top_processes: list[dict[str, object]]
    raw_all: dict[str, object]

    # ------------------------------------------------------------------
    # Convenience helpers
    # ------------------------------------------------------------------

    def as_text(self, locale: str = "en") -> str:
        """Return a compact multi-line text representation."""
        from app.utils.i18n import t  # local import to avoid circular deps at module load

        lines = [
            f"{t('status.cpu', locale=locale)}: {self.cpu_percent:.1f}%",
            f"{t('status.ram', locale=locale)}: {self.ram_percent:.1f}%"
            f" ({self.ram_used_gb:.1f} / {self.ram_total_gb:.1f} GB)",
            f"{t('status.disk', locale=locale)}: {self.disk_percent:.1f}%"
            f" ({self.disk_used_gb:.1f} / {self.disk_total_gb:.1f} GB)",
            (
                f"{t('status.load', locale=locale)}: "
                f"{self.load_1:.2f} / {self.load_5:.2f} / {self.load_15:.2f}"
            ),
            f"{t('status.uptime', locale=locale)}: {self.uptime}",
            f"{t('status.docker', locale=locale)}: {self.docker_running} / {self.docker_total}",
        ]
        if self.top_processes:
            lines.append("")
            lines.append("Top processes (CPU)")
            for proc in self.top_processes[:5]:
                name = proc.get("name") or "?"
                cpu_pct = _num(proc.get("cpu_percent", 0))
                mem_pct = _num(proc.get("memory_percent", 0))
                lines.append(f"  - {name} | CPU {cpu_pct:.1f}% | RAM {mem_pct:.1f}%")
        return "\n".join(lines)

    def as_raw_json(self) -> str:
        """Return aggregated Glances payload serialized as JSON."""
        return json.dumps(self.raw_all, ensure_ascii=True)


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

    snapshot = await _fetch_snapshot()
    _cached_snapshot = snapshot
    _cache_timestamp = now
    return snapshot


async def _fetch_snapshot() -> ServerSnapshot:
    """Internal: always performs live HTTP requests to Glances."""
    cfg = get_config()
    base_url = cfg.glances_base_url.rstrip("/")

    async with httpx.AsyncClient(timeout=_request_timeout()) as client:
        payload = await _fetch_all(client, base_url)

    if cfg.glances_log_full_payload:
        logger.info("Glances aggregated payload: %s", json.dumps(payload, ensure_ascii=True))
    else:
        logger.debug("Glances aggregated payload keys: %s", ", ".join(sorted(payload.keys())))

    cpu = _as_dict(payload.get("cpu"))
    mem = _as_dict(payload.get("mem"))
    fs_data = _as_list_of_dicts(payload.get("fs"))
    disk = _pick_root_fs(fs_data)
    load = _as_dict(payload.get("load"))
    uptime = payload.get("uptime")
    containers = _as_list_of_dicts(payload.get("containers"))
    processes = _as_list_of_dicts(payload.get("processlist"))

    gb = 1024**3

    disk_used = _num(disk.get("used", 0))
    disk_size = _num(disk.get("size", 1))
    disk_percent = _num(disk.get("percent", 0.0))

    docker_all = containers
    docker_running = sum(1 for c in docker_all if c.get("status") == "running")

    top = sorted(
        processes,
        key=lambda p: _num(p.get("cpu_percent", 0)),
        reverse=True,
    )

    return ServerSnapshot(
        cpu_percent=_num(cpu.get("total", 0.0)),
        ram_percent=_num(mem.get("percent", 0.0)),
        ram_used_gb=_num(mem.get("used", 0)) / gb,
        ram_total_gb=max(_num(mem.get("total", 0)), 1.0) / gb,
        disk_percent=float(disk_percent),
        disk_used_gb=disk_used / gb,
        disk_total_gb=disk_size / gb,
        load_1=_num(load.get("min1", 0.0)),
        load_5=_num(load.get("min5", 0.0)),
        load_15=_num(load.get("min15", 0.0)),
        uptime=str(uptime).strip('"') if uptime else "n/a",
        docker_running=docker_running,
        docker_total=len(docker_all),
        top_processes=top[:5],
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
            response = await client.get(f"{base_url}{path}")
            response.raise_for_status()
            return key, response.json()
        except Exception as exc:  # noqa: BLE001
            return key, {"_error": str(exc), "_endpoint": path}

    results = await asyncio.gather(
        *(_fetch_endpoint(key, path) for key, path in _ENDPOINTS),
    )
    return {key: value for key, value in results}


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
    return max(fs_list, key=lambda e: _num(e.get("size", 0)))
