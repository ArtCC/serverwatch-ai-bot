"""Proactive alert scheduler.

Runs a periodic job (every ALERT_CHECK_INTERVAL_SECONDS) that fetches
Glances metrics and sends a Telegram message when any threshold is
exceeded. A per-metric cooldown (ALERT_COOLDOWN_SECONDS) prevents
repeated notifications for the same condition.
"""

from __future__ import annotations

import logging
import time

from telegram.constants import ParseMode
from telegram.ext import Application, ContextTypes

from app.core import store
from app.core.config import get_config
from app.services import glances
from app.utils.i18n import t

logger = logging.getLogger("serverwatch")

# context.bot_data keys
_BD_LAST_ALERT: str = "alert_last_sent"  # dict[str, float] metric -> epoch


def _last_alerts(context: ContextTypes.DEFAULT_TYPE) -> dict[str, float]:
    data = context.bot_data.setdefault(_BD_LAST_ALERT, {})
    if not isinstance(data, dict):
        context.bot_data[_BD_LAST_ALERT] = {}
        return context.bot_data[_BD_LAST_ALERT]
    return data


async def check_and_alert(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Fetch metrics and fire alerts for any metric above its threshold."""
    cfg = get_config()
    cooldown = cfg.alert_cooldown_seconds
    now = time.monotonic()
    last = _last_alerts(context)

    try:
        snapshot = await glances.get_snapshot()
    except Exception:
        logger.warning("Alert check: could not fetch Glances snapshot")
        return

    cpu_t, ram_t, disk_t = await store.get_thresholds()
    thresholds = {
        "cpu": (snapshot.cpu_percent, cpu_t),
        "ram": (snapshot.ram_percent, ram_t),
        "disk": (snapshot.disk_percent, disk_t),
    }

    locale = cfg.bot_locale

    for metric, (value, threshold) in thresholds.items():
        if value < threshold:
            # Metric back below threshold — reset cooldown so next breach triggers immediately.
            last.pop(metric, None)
            continue

        last_sent = last.get(metric, 0.0)
        if now - last_sent < cooldown:
            logger.debug(
                "Alert suppressed for %s (value=%.1f%% threshold=%.1f%% cooldown remaining=%.0fs)",
                metric,
                value,
                threshold,
                cooldown - (now - last_sent),
            )
            continue

        last[metric] = now
        text = t(
            f"alerts_notification.{metric}",
            locale=locale,
            value=round(value, 1),
            threshold=round(threshold, 1),
        )
        logger.info("Sending alert: %s=%.1f%% (threshold=%.1f%%)", metric, value, threshold)
        try:
            await context.bot.send_message(
                chat_id=cfg.telegram_chat_id,
                text=text,
                parse_mode=ParseMode.MARKDOWN,
            )
        except Exception:
            logger.exception("Failed to send alert message for metric=%s", metric)
            # Roll back the timestamp so the alert retries on next cycle.
            last[metric] = last_sent


def register(app: Application) -> None:
    """Register the periodic alert check job on the Application's job queue."""
    cfg = get_config()
    interval = cfg.alert_check_interval_seconds
    if interval <= 0:
        logger.info("Alert scheduler disabled (ALERT_CHECK_INTERVAL_SECONDS=%d)", interval)
        return

    if app.job_queue is None:
        logger.error(
            "Job queue is not available — alert scheduler cannot start. "
            "Ensure python-telegram-bot is installed with the [job-queue] extra."
        )
        return

    app.job_queue.run_repeating(
        check_and_alert,
        interval=interval,
        first=interval,
        name="alert_check",
    )
    logger.info(
        "Alert scheduler registered (interval=%ds cooldown=%ds)",
        interval,
        cfg.alert_cooldown_seconds,
    )
