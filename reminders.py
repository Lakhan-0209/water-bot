"""
reminders.py – per-user reminder scheduler using APScheduler.
Each active user gets their own IntervalTrigger job, respecting their
personal start/end window and interval (in minutes).
"""

import logging
from datetime import datetime
import pytz

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger

import database as db

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()


# ── Internal helpers ───────────────────────────────────────────────────────────

def _in_window(user: dict) -> bool:
    """Return True if *now* (in user's timezone) is within reminder window."""
    try:
        tz    = pytz.timezone(user["timezone"])
        now   = datetime.now(tz)
        start = datetime.strptime(user["reminder_start"], "%H:%M").time()
        end   = datetime.strptime(user["reminder_end"],   "%H:%M").time()
        return start <= now.time() <= end
    except Exception:
        return True          # default: always send if tz is bad


async def _send_reminder(app, user_id: int):
    """Fire a reminder to one user if they're in their window."""
    user = db.get_user(user_id)
    if not user or not user["active"]:
        return

    if not _in_window(user):
        return

    today  = db.get_today_total(user_id)
    goal   = user["daily_goal"]
    pct    = min(int(today / goal * 100), 100)
    remain = max(goal - today, 0)

    if today >= goal:
        msg = (
            f"💧 You've already hit your daily goal of {goal} ml — "
            f"amazing! Keep sipping to stay hydrated. 🎉"
        )
    else:
        bar_filled = pct // 10
        bar        = "🟦" * bar_filled + "⬜" * (10 - bar_filled)
        msg = (
            f"💧 *Time to drink water!*\n\n"
            f"{bar} {pct}%\n"
            f"Today: *{today} ml* / {goal} ml\n"
            f"Remaining: *{remain} ml*\n\n"
            f"_Quick log:_ /log\\_250 · /log\\_500"
        )

    try:
        await app.bot.send_message(
            chat_id=user_id,
            text=msg,
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.warning("Could not send reminder to %s: %s", user_id, e)


# ── Public API ─────────────────────────────────────────────────────────────────

def start_scheduler(app):
    """Start the scheduler and register all active users."""
    if not scheduler.running:
        scheduler.start()

    # Master job: refresh user list every 5 minutes
    scheduler.add_job(
        _refresh_all_jobs,
        CronTrigger(minute="*/5"),
        args=[app],
        id="__refresh__",
        replace_existing=True,
    )

    # Seed immediately
    import asyncio
    loop = asyncio.get_event_loop()
    loop.create_task(_refresh_all_jobs(app))


async def _refresh_all_jobs(app):
    """Sync scheduler jobs with current DB state."""
    users = db.get_all_active_users()
    active_ids = {u["user_id"] for u in users}

    # Remove jobs for users who deactivated
    for job in scheduler.get_jobs():
        if job.id.startswith("remind_"):
            uid = int(job.id.split("_")[1])
            if uid not in active_ids:
                job.remove()

    # Add/update jobs for active users
    for user in users:
        uid      = user["user_id"]
        interval = max(user["reminder_interval"], 15)   # minimum 15 min
        job_id   = f"remind_{uid}"

        scheduler.add_job(
            _send_reminder,
            IntervalTrigger(minutes=interval),
            args=[app, uid],
            id=job_id,
            replace_existing=True,
            max_instances=1,
        )


def reschedule_user(app, user_id: int):
    """Call this after a user changes their settings."""
    user   = db.get_user(user_id)
    if not user or not user["active"]:
        job_id = f"remind_{user_id}"
        if scheduler.get_job(job_id):
            scheduler.remove_job(job_id)
        return

    interval = max(user["reminder_interval"], 15)
    job_id   = f"remind_{user_id}"
    scheduler.add_job(
        _send_reminder,
        IntervalTrigger(minutes=interval),
        args=[app, user_id],
        id=job_id,
        replace_existing=True,
        max_instances=1,
    )
