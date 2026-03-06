"""Async client for the Glances REST API v4.

Only the endpoints needed for the bot are implemented.
All functions raise httpx.HTTPError on connectivity / HTTP errors.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

import httpx

from app.core.config import get_config

logger = logging.getLogger("serverwatch")

_TIMEOUT = 8.0


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

    def as_text(self) -> str:
        """Return a compact multi-line text representation."""
        lines = [
            f"CPU: {self.cpu_percent:.1f}%",
            f"RAM: {self.ram_percent:.1f}% ({self.ram_used_gb:.1f} / {self.ram_total_gb:.1f} GB)",
            f"Disk: {self.disk_percent:.1f}% "
            f"({self.disk_used_gb:.1f} / {self.disk_total_gb:.1f} GB)",
            f"Load avg: {self.load_1:.2f} / {self.load_5:.2f} / {self.load_15:.2f}",
            f"Uptime: {self.uptime}",
            f"Docker: {self.docker_running} running / {self.docker_total} total",
        ]
        if self.top_processes:
            lines.append("")
            lines.append("Top processes (CPU)")
            for proc in self.top_processes[:5]:
                lines.append(
                    f"  - {proc['name']} | CPU {proc['cpu_percent']:.1f}% | "
                    f"RAM {proc['memory_percent']:.1f}%"
                )
        return "\n".join(lines)

    def as_raw_json(self) -> str:
        """Return raw /all payload serialized as JSON."""
        return json.dumps(self.raw_all, ensure_ascii=True)


async def get_snapshot() -> ServerSnapshot:
    """Fetch /all once and return a ServerSnapshot."""
    base_url = get_config().glances_base_url.rstrip("/")

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        all_r = await _fetch_all(client, base_url)

    payload = all_r.json()
    if not isinstance(payload, dict):
        payload = {}

    logger.info("Glances /all payload: %s", json.dumps(payload, ensure_ascii=True))

    cpu = payload.get("cpu") if isinstance(payload.get("cpu"), dict) else {}
    mem = payload.get("mem") if isinstance(payload.get("mem"), dict) else {}
    fs_data = payload.get("fs") if isinstance(payload.get("fs"), list) else []
    disk = _pick_root_fs([e for e in fs_data if isinstance(e, dict)])
    load = payload.get("load") if isinstance(payload.get("load"), dict) else {}
    uptime = payload.get("uptime")
    containers = payload.get("containers") if isinstance(payload.get("containers"), list) else []
    processes = payload.get("processlist") if isinstance(payload.get("processlist"), list) else []

    gb = 1024**3

    disk_used = _num(disk.get("used", 0))
    disk_size = _num(disk.get("size", 1))
    disk_percent = _num(disk.get("percent", 0.0))

    docker_all = containers if isinstance(containers, list) else containers.get("containers", [])
    docker_running = sum(1 for c in docker_all if c.get("status") == "running")

    top = sorted(
        [p for p in processes if isinstance(p, dict)],
        key=lambda p: p.get("cpu_percent", 0),
        reverse=True,
    )

    return ServerSnapshot(
        cpu_percent=float(cpu.get("total", 0.0)),
        ram_percent=float(mem.get("percent", 0.0)),
        ram_used_gb=_num(mem.get("used", 0)) / gb,
        ram_total_gb=max(_num(mem.get("total", 0)), 1.0) / gb,
        disk_percent=float(disk_percent),
        disk_used_gb=disk_used / gb,
        disk_total_gb=disk_size / gb,
        load_1=float(load.get("min1", 0.0)),
        load_5=float(load.get("min5", 0.0)),
        load_15=float(load.get("min15", 0.0)),
        uptime=str(uptime).strip('"') if uptime else "n/a",
        docker_running=docker_running,
        docker_total=len(docker_all),
        top_processes=top[:5],
        raw_all=payload,
    )


async def _fetch_all(
    client: httpx.AsyncClient, base_url: str
) -> httpx.Response:
    """Fetch full metrics payload from /all endpoint."""
    response = await client.get(f"{base_url}/all")
    response.raise_for_status()
    return response


def _num(value: object) -> float:
    """Safely cast an API value to float."""
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0


def _pick_root_fs(fs_list: list[dict[str, object]]) -> dict[str, object]:
    """Return the root filesystem entry, or the largest one as fallback."""
    if not fs_list:
        return {}
    for entry in fs_list:
        if entry.get("mnt_point") == "/":
            return entry
    return max(fs_list, key=lambda e: _num(e.get("size", 0)))
