"""
bot.py – Water Reminder Telegram Bot (compatible with python-telegram-bot v21+)
"""

import logging
import os
import re

import pytz
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import database as db
import reminders

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

TOKEN = "8608037770:AAFPO-adUUJjHiyipaKqEz-s36-HpNrUEZY"

# ── Helpers ────────────────────────────────────────────────────────────────────

def _register(update: Update):
    u = update.effective_user
    db.upsert_user(u.id, u.username or "", u.first_name or "friend")
    return db.get_user(u.id)


def _progress_bar(current, goal, width=10):
    pct    = min(current / goal, 1.0)
    filled = round(pct * width)
    return "🟦" * filled + "⬜" * (width - filled), int(pct * 100)


def _week_chart(stats, goal):
    lines = []
    for row in stats:
        total = row["total"]
        bar, pct = _progress_bar(total, goal, width=8)
        tick = "✅" if total >= goal else "  "
        lines.append(f"{row['day'][5:]}  {bar} {total:>5} ml {tick}")
    return "\n".join(lines) if lines else "No data yet – start logging! /log 250"


# ── Command handlers ───────────────────────────────────────────────────────────

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = _register(update)
    name = user["first_name"]

    keyboard = ReplyKeyboardMarkup(
        [[KeyboardButton("💧 Log 250 ml"), KeyboardButton("💧 Log 500 ml")],
         [KeyboardButton("📊 Today"),      KeyboardButton("📈 Stats")]],
        resize_keyboard=True,
    )

    await update.message.reply_text(
        f"👋 Hi *{name}*! I'm your personal Water Reminder bot.\n\n"
        f"🥅 Daily goal: *{user['daily_goal']} ml*\n"
        f"⏰ Reminders: every *{user['reminder_interval']} min* "
        f"({user['reminder_start']} – {user['reminder_end']})\n"
        f"🌍 Timezone: *{user['timezone']}*\n\n"
        f"Start logging with /log 250 or tap the buttons below!\n"
        f"See all commands with /help",
        parse_mode="Markdown",
        reply_markup=keyboard,
    )

    reminders.reschedule_user(ctx.application, update.effective_user.id)


async def cmd_help(update: Update, _):
    await update.message.reply_text(
        "*All Commands*\n\n"
        "💧 *Logging*\n"
        "/log `<ml>` – log any amount (e.g. /log 350)\n"
        "/log\\_250 · /log\\_500 · /log\\_750 – quick shortcuts\n\n"
        "📊 *Stats*\n"
        "/today – today's progress\n"
        "/stats – past 7 days\n"
        "/month – this month\n"
        "/streak – goal streak\n\n"
        "⚙️ *Settings*\n"
        "/goal `<ml>` – daily goal (default 2500)\n"
        "/interval `<min>` – reminder every N minutes (min 15)\n"
        "/window `HH:MM` `HH:MM` – active hours\n"
        "/timezone `<tz>` – e.g. Asia/Kolkata, UTC, US/Eastern\n\n"
        "🔔 *Reminders*\n"
        "/pause – stop reminders\n"
        "/resume – restart reminders",
        parse_mode="Markdown",
    )


async def cmd_log(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = _register(update)
    uid  = update.effective_user.id

    text = update.message.text or ""
    match = re.search(r"\d+", text)
    if not match:
        await update.message.reply_text("Usage: /log 250  (amount in ml)")
        return

    amount = int(match.group())
    if not (10 <= amount <= 5000):
        await update.message.reply_text("Please log between 10 and 5000 ml.")
        return

    db.log_water(uid, amount)
    today = db.get_today_total(uid)
    goal  = user["daily_goal"]
    bar, pct = _progress_bar(today, goal)

    extra = ""
    if today >= goal:
        extra = "\n\n🎉 *Goal reached!* Amazing work today."

    await update.message.reply_text(
        f"✅ Logged *{amount} ml*\n\n"
        f"{bar} {pct}%\n"
        f"Today: *{today} ml* / {goal} ml{extra}",
        parse_mode="Markdown",
    )


async def cmd_today(update: Update, _):
    user  = _register(update)
    uid   = update.effective_user.id
    today = db.get_today_total(uid)
    goal  = user["daily_goal"]
    bar, pct = _progress_bar(today, goal)
    remain = max(goal - today, 0)

    status = "🎉 Goal reached!" if today >= goal else f"Need *{remain} ml* more"

    await update.message.reply_text(
        f"📊 *Today's Progress*\n\n"
        f"{bar} {pct}%\n"
        f"Drank: *{today} ml* / {goal} ml\n"
        f"{status}",
        parse_mode="Markdown",
    )


async def cmd_stats(update: Update, _):
    user  = _register(update)
    uid   = update.effective_user.id
    stats = db.get_week_stats(uid)
    chart = _week_chart(stats, user["daily_goal"])

    await update.message.reply_text(
        f"📈 *Last 7 Days*\n\n`{chart}`",
        parse_mode="Markdown",
    )


async def cmd_month(update: Update, _):
    user  = _register(update)
    uid   = update.effective_user.id
    stats = db.get_month_stats(uid)
    goal  = user["daily_goal"]

    if not stats:
        await update.message.reply_text("No data this month yet!")
        return

    days_met    = sum(1 for r in stats if r["total"] >= goal)
    total_ml    = sum(r["total"] for r in stats)
    days_logged = len(stats)
    avg         = total_ml // days_logged if days_logged else 0
    chart       = _week_chart(stats[-7:], goal)

    await update.message.reply_text(
        f"📅 *This Month*\n\n"
        f"Days logged: *{days_logged}*\n"
        f"Days goal met: *{days_met}*\n"
        f"Total: *{total_ml} ml*\n"
        f"Daily average: *{avg} ml*\n\n"
        f"*Recent 7 days:*\n`{chart}`",
        parse_mode="Markdown",
    )


async def cmd_streak(update: Update, _):
    user   = _register(update)
    uid    = update.effective_user.id
    streak = db.get_streak(uid, user["daily_goal"])

    emoji = "🔥" if streak >= 3 else "💧"
    msg   = (
        f"{emoji} *{streak}-day streak!* Keep it up!"
        if streak > 0
        else "No streak yet — log today to start one! /log 250"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")


async def cmd_goal(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    _register(update)
    uid = update.effective_user.id

    if not ctx.args:
        await update.message.reply_text("Usage: /goal 2500  (ml per day)")
        return
    try:
        goal = int(ctx.args[0])
        assert 500 <= goal <= 10000
    except (ValueError, AssertionError):
        await update.message.reply_text("Goal must be between 500 and 10000 ml.")
        return

    db.set_user_field(uid, "daily_goal", goal)
    await update.message.reply_text(f"✅ Daily goal set to *{goal} ml*.", parse_mode="Markdown")


async def cmd_interval(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    _register(update)
    uid = update.effective_user.id

    if not ctx.args:
        await update.message.reply_text("Usage: /interval 60  (minutes, min 15)")
        return
    try:
        mins = int(ctx.args[0])
        assert 15 <= mins <= 480
    except (ValueError, AssertionError):
        await update.message.reply_text("Interval must be between 15 and 480 minutes.")
        return

    db.set_user_field(uid, "reminder_interval", mins)
    reminders.reschedule_user(ctx.application, uid)
    await update.message.reply_text(
        f"✅ Reminders set to every *{mins} minutes*.", parse_mode="Markdown"
    )


async def cmd_window(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    _register(update)
    uid = update.effective_user.id

    if len(ctx.args) < 2:
        await update.message.reply_text("Usage: /window 08:00 22:00")
        return

    def valid_time(s):
        return re.fullmatch(r"\d{2}:\d{2}", s)

    if not valid_time(ctx.args[0]) or not valid_time(ctx.args[1]):
        await update.message.reply_text("Format: HH:MM  e.g. /window 07:00 23:00")
        return

    db.set_user_field(uid, "reminder_start", ctx.args[0])
    db.set_user_field(uid, "reminder_end",   ctx.args[1])
    reminders.reschedule_user(ctx.application, uid)
    await update.message.reply_text(
        f"✅ Reminder window: *{ctx.args[0]} – {ctx.args[1]}*.", parse_mode="Markdown"
    )


async def cmd_timezone(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    _register(update)
    uid = update.effective_user.id

    if not ctx.args:
        await update.message.reply_text(
            "Usage: /timezone Asia/Kolkata\n"
            "Common: UTC, US/Eastern, US/Pacific, Europe/London, Asia/Kolkata"
        )
        return

    tz_str = ctx.args[0]
    try:
        pytz.timezone(tz_str)
    except pytz.UnknownTimeZoneError:
        await update.message.reply_text(
            f"Unknown timezone: {tz_str}\n"
            "Find yours at https://en.wikipedia.org/wiki/List_of_tz_database_time_zones"
        )
        return

    db.set_user_field(uid, "timezone", tz_str)
    await update.message.reply_text(f"✅ Timezone set to *{tz_str}*.", parse_mode="Markdown")


async def cmd_pause(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    _register(update)
    uid = update.effective_user.id
    db.set_user_field(uid, "active", 0)
    reminders.reschedule_user(ctx.application, uid)
    await update.message.reply_text("⏸ Reminders paused. Use /resume to restart.")


async def cmd_resume(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    _register(update)
    uid = update.effective_user.id
    db.set_user_field(uid, "active", 1)
    reminders.reschedule_user(ctx.application, uid)
    await update.message.reply_text("▶️ Reminders resumed! Stay hydrated 💧")


# ── Keyboard button handler ────────────────────────────────────────────────────

async def handle_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    if text == "💧 Log 250 ml":
        update.message.text = "/log 250"
        await cmd_log(update, ctx)
    elif text == "💧 Log 500 ml":
        update.message.text = "/log 500"
        await cmd_log(update, ctx)
    elif text == "📊 Today":
        await cmd_today(update, ctx)
    elif text == "📈 Stats":
        await cmd_stats(update, ctx)


# ── App bootstrap ──────────────────────────────────────────────────────────────

def main():
    db.init_db()

    async def post_init(application):
        reminders.start_scheduler(application)

    app = (
        Application.builder()
        .token(TOKEN)
        .post_init(post_init)
        .build()
    )

    app.add_handler(CommandHandler("start",    cmd_start))
    app.add_handler(CommandHandler("help",     cmd_help))
    app.add_handler(CommandHandler("log",      cmd_log))
    app.add_handler(CommandHandler("log_250",  cmd_log))
    app.add_handler(CommandHandler("log_500",  cmd_log))
    app.add_handler(CommandHandler("log_750",  cmd_log))
    app.add_handler(CommandHandler("today",    cmd_today))
    app.add_handler(CommandHandler("stats",    cmd_stats))
    app.add_handler(CommandHandler("month",    cmd_month))
    app.add_handler(CommandHandler("streak",   cmd_streak))
    app.add_handler(CommandHandler("goal",     cmd_goal))
    app.add_handler(CommandHandler("interval", cmd_interval))
    app.add_handler(CommandHandler("window",   cmd_window))
    app.add_handler(CommandHandler("timezone", cmd_timezone))
    app.add_handler(CommandHandler("pause",    cmd_pause))
    app.add_handler(CommandHandler("resume",   cmd_resume))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    logger.info("Bot starting…")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
