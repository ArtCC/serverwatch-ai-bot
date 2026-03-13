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
from telegram.helpers import escape_markdown

from app.core import store
from app.core.config import get_config
from app.services import glances
from app.utils.i18n import t

logger = logging.getLogger("serverwatch")

# context.bot_data keys
_BD_LAST_ALERT: str = "alert_last_sent"  # dict[str, float] metric -> epoch
_BD_METRIC_STATE: str = "alert_metric_state"  # dict[str, dict[str, float | int | bool]]
_BD_METRIC_SAMPLES: str = "alert_metric_samples"  # dict[str, list[float]]


def _last_alerts(context: ContextTypes.DEFAULT_TYPE) -> dict[str, float]:
    data = context.bot_data.setdefault(_BD_LAST_ALERT, {})
    if not isinstance(data, dict):
        context.bot_data[_BD_LAST_ALERT] = {}
        return context.bot_data[_BD_LAST_ALERT]
    return data


def _metric_states(context: ContextTypes.DEFAULT_TYPE) -> dict[str, dict[str, object]]:
    data = context.bot_data.setdefault(_BD_METRIC_STATE, {})
    if not isinstance(data, dict):
        context.bot_data[_BD_METRIC_STATE] = {}
        return context.bot_data[_BD_METRIC_STATE]
    return data


def _metric_samples(context: ContextTypes.DEFAULT_TYPE) -> dict[str, list[float]]:
    data = context.bot_data.setdefault(_BD_METRIC_SAMPLES, {})
    if not isinstance(data, dict):
        context.bot_data[_BD_METRIC_SAMPLES] = {}
        return context.bot_data[_BD_METRIC_SAMPLES]
    return data


def _state_for_metric(states: dict[str, dict[str, object]], metric: str) -> dict[str, object]:
    current = states.get(metric)
    if not isinstance(current, dict):
        current = {"breaches": 0, "active": False, "first_breach": 0.0}
        states[metric] = current

    breaches = current.get("breaches", 0)
    active = current.get("active", False)
    first_breach = current.get("first_breach", 0.0)

    current["breaches"] = int(breaches) if isinstance(breaches, (int, float)) else 0
    current["active"] = bool(active)
    current["first_breach"] = float(first_breach) if isinstance(first_breach, (int, float)) else 0.0
    return current


def _append_sample(
    samples: dict[str, list[float]], metric: str, value: float, window_size: int
) -> list[float]:
    values = samples.get(metric)
    if not isinstance(values, list):
        values = []
    values.append(value)
    keep = max(1, window_size)
    if len(values) > keep:
        values = values[-keep:]
    samples[metric] = values
    return values


def _state_breaches(state: dict[str, object]) -> int:
    raw = state.get("breaches", 0)
    return int(raw) if isinstance(raw, (int, float)) else 0


def _state_first_breach(state: dict[str, object]) -> float:
    raw = state.get("first_breach", 0.0)
    return float(raw) if isinstance(raw, (int, float)) else 0.0


async def check_and_alert(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Fetch metrics and fire alerts for any metric above its threshold."""
    cfg = get_config()
    cooldown = cfg.alert_cooldown_seconds
    required_breaches = max(1, cfg.alert_consecutive_breaches)
    recovery_margin = max(0.0, cfg.alert_recovery_margin_percent)
    context_window = max(1, cfg.alert_context_window_samples)
    now = time.monotonic()
    last = _last_alerts(context)
    states = _metric_states(context)
    samples = _metric_samples(context)

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
        recent_values = _append_sample(samples, metric, value, context_window)
        state = _state_for_metric(states, metric)

        recovery_threshold = max(0.0, threshold - recovery_margin)
        if value < recovery_threshold:
            # Recovery uses hysteresis to avoid flapping near the threshold.
            last.pop(metric, None)
            state["breaches"] = 0
            state["active"] = False
            state["first_breach"] = 0.0
            continue

        if value < threshold:
            # Keep state in the hysteresis band to avoid noisy transitions.
            continue

        breaches = _state_breaches(state) + 1
        state["breaches"] = breaches
        if breaches == 1:
            state["first_breach"] = now

        if breaches < required_breaches:
            logger.debug(
                "Alert candidate for %s (value=%.1f%% threshold=%.1f%% breach=%d/%d)",
                metric,
                value,
                threshold,
                breaches,
                required_breaches,
            )
            continue

        state["active"] = True

        last_sent = last.get(metric, 0.0)
        has_last_sent = metric in last
        if has_last_sent and now - last_sent < cooldown:
            logger.debug(
                "Alert suppressed for %s (value=%.1f%% threshold=%.1f%% cooldown remaining=%.0fs)",
                metric,
                value,
                threshold,
                cooldown - (now - last_sent),
            )
            continue

        last[metric] = now
        sustained_seconds = max(0.0, now - _state_first_breach(state))
        avg_value = sum(recent_values) / len(recent_values)
        headline = t(
            f"alerts_notification.{metric}",
            locale=locale,
            value=round(value, 1),
            threshold=round(threshold, 1),
        )
        context_line = t(
            "alerts_notification.context",
            locale=locale,
            avg=round(avg_value, 1),
            samples=len(recent_values),
            sustained=max(1, int(sustained_seconds)),
        )
        text = f"{headline}\n{context_line}"
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

    # Global health alert based on the enriched snapshot scoring layer.
    # Warning and critical are treated separately to avoid duplicated spam.
    health_metric = f"health:{snapshot.health_level}"
    if snapshot.health_level == "good":
        last.pop("health:warning", None)
        last.pop("health:critical", None)
        return

    health_last_sent = last.get(health_metric, 0.0)
    has_health_last_sent = health_metric in last
    if has_health_last_sent and now - health_last_sent < cooldown:
        logger.debug(
            "Health alert suppressed (level=%s score=%d cooldown remaining=%.0fs)",
            snapshot.health_level,
            snapshot.health_score,
            cooldown - (now - health_last_sent),
        )
        return

    last[health_metric] = now
    icon = "⚠️" if snapshot.health_level == "warning" else "❌"
    details = escape_markdown("; ".join(snapshot.key_findings[:2]))
    action = escape_markdown(snapshot.recommended_action)
    text = (
        f"{icon} *Health alert*: level *{snapshot.health_level.upper()}* "
        f"(score: {snapshot.health_score}/100)\n"
        f"Top findings: {details}\n"
        f"Action: {action}"
    )
    logger.info(
        "Sending health alert level=%s score=%d",
        snapshot.health_level,
        snapshot.health_score,
    )
    try:
        await context.bot.send_message(
            chat_id=cfg.telegram_chat_id,
            text=text,
            parse_mode=ParseMode.MARKDOWN,
        )
    except Exception:
        logger.exception("Failed to send health alert")
        last[health_metric] = health_last_sent


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
