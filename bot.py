from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import sqlite3
import datetime
import os

# ==========================================
# BOT TOKEN (set via environment variable for Railway)
# ==========================================

BOT_TOKEN = os.environ.get("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")

# ==========================================
# DATABASE SETUP
# ==========================================

conn = sqlite3.connect("users.db", check_same_thread=False)
cursor = conn.cursor()

cursor.executescript("""
CREATE TABLE IF NOT EXISTS users (
    chat_id     INTEGER PRIMARY KEY,
    reminder_interval INTEGER DEFAULT 60,
    daily_goal  INTEGER DEFAULT 8,
    active      INTEGER DEFAULT 1,
    timezone    TEXT DEFAULT 'UTC'
);

CREATE TABLE IF NOT EXISTS water_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id     INTEGER,
    logged_at   TEXT,
    glasses     INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS streaks (
    chat_id     INTEGER PRIMARY KEY,
    current_streak  INTEGER DEFAULT 0,
    longest_streak  INTEGER DEFAULT 0,
    last_active_date TEXT
);
""")
conn.commit()

# ==========================================
# SCHEDULER
# ==========================================

scheduler = AsyncIOScheduler()

# ==========================================
# HELPERS
# ==========================================

def get_user(chat_id):
    cursor.execute("SELECT * FROM users WHERE chat_id=?", (chat_id,))
    return cursor.fetchone()

def today_str():
    return datetime.datetime.utcnow().strftime("%Y-%m-%d")

def glasses_today(chat_id):
    cursor.execute(
        "SELECT COALESCE(SUM(glasses),0) FROM water_log WHERE chat_id=? AND date(logged_at)=date('now')",
        (chat_id,)
    )
    return cursor.fetchone()[0]

def update_streak(chat_id):
    today = today_str()
    cursor.execute("SELECT * FROM streaks WHERE chat_id=?", (chat_id,))
    row = cursor.fetchone()
    if not row:
        cursor.execute(
            "INSERT INTO streaks VALUES (?,1,1,?)", (chat_id, today)
        )
    else:
        _, current, longest, last_date = row
        if last_date == today:
            return  # already updated today
        yesterday = (datetime.datetime.utcnow() - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
        if last_date == yesterday:
            current += 1
        else:
            current = 1
        longest = max(longest, current)
        cursor.execute(
            "UPDATE streaks SET current_streak=?, longest_streak=?, last_active_date=? WHERE chat_id=?",
            (current, longest, today, chat_id)
        )
    conn.commit()

def get_streak(chat_id):
    cursor.execute("SELECT current_streak, longest_streak FROM streaks WHERE chat_id=?", (chat_id,))
    row = cursor.fetchone()
    return row if row else (0, 0)

def progress_bar(current, goal, length=10):
    filled = int((current / goal) * length) if goal > 0 else 0
    filled = min(filled, length)
    return "🟦" * filled + "⬜" * (length - filled)

def log_water_glass(chat_id, glasses=1):
    cursor.execute(
        "INSERT INTO water_log (chat_id, logged_at, glasses) VALUES (?, datetime('now'), ?)",
        (chat_id, glasses)
    )
    conn.commit()

    # Check if goal met today → update streak
    user = get_user(chat_id)
    if user:
        goal = user[2]
        if glasses_today(chat_id) >= goal:
            update_streak(chat_id)

# ==========================================
# SEND REMINDER
# ==========================================

async def send_reminder(bot, chat_id):
    try:
        user = get_user(chat_id)
        if not user or not user[3]:  # inactive
            return

        goal = user[2]
        drunk = glasses_today(chat_id)
        remaining = max(0, goal - drunk)
        bar = progress_bar(drunk, goal)

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("💧 Log 1 glass", callback_data="log_1"),
                InlineKeyboardButton("💧💧 Log 2 glasses", callback_data="log_2"),
            ],
            [InlineKeyboardButton("📊 Today's Stats", callback_data="stats_today")]
        ])

        await bot.send_message(
            chat_id=chat_id,
            text=(
                f"⏰ *Time to drink water!*\n\n"
                f"{bar}\n"
                f"✅ Drank: *{drunk}/{goal}* glasses today\n"
                f"🎯 Remaining: *{remaining}* glasses\n\n"
                f"Tap below to log your glass!"
            ),
            parse_mode="Markdown",
            reply_markup=keyboard
        )
    except Exception as e:
        print(f"Reminder error for {chat_id}:", e)

# ==========================================
# /start COMMAND
# ==========================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    name = update.effective_chat.first_name or "there"

    cursor.execute(
        "INSERT OR IGNORE INTO users (chat_id) VALUES (?)", (chat_id,)
    )
    cursor.execute(
        "UPDATE users SET active=1 WHERE chat_id=?", (chat_id,)
    )
    conn.commit()

    user = get_user(chat_id)
    interval = user[1]

    # Reschedule
    try:
        scheduler.remove_job(str(chat_id))
    except:
        pass

    scheduler.add_job(
        send_reminder,
        "interval",
        minutes=interval,
        args=[context.bot, chat_id],
        id=str(chat_id)
    )

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("💧 Log Water", callback_data="log_1"),
            InlineKeyboardButton("📊 My Stats", callback_data="stats_today"),
        ],
        [
            InlineKeyboardButton("🏆 Streaks", callback_data="streaks"),
            InlineKeyboardButton("⚙️ Settings", callback_data="settings"),
        ]
    ])

    await update.message.reply_text(
        f"👋 Hey *{name}*! Welcome to your personal Water Reminder Bot! 💧\n\n"
        f"I'll remind you every *{interval} minutes* to drink water.\n"
        f"Your daily goal: *8 glasses* 🎯\n\n"
        f"*Commands:*\n"
        f"/set `<mins>` — Change reminder interval\n"
        f"/goal `<glasses>` — Set daily goal\n"
        f"/log — Log a glass of water\n"
        f"/stats — Today's stats\n"
        f"/weekly — Weekly report\n"
        f"/streak — Your streak\n"
        f"/stop — Pause reminders\n"
        f"/help — Show all commands",
        parse_mode="Markdown",
        reply_markup=keyboard
    )

# ==========================================
# /set COMMAND
# ==========================================

async def set_timer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id

    if not context.args:
        await update.message.reply_text("Usage: /set `30` (minutes)", parse_mode="Markdown")
        return

    try:
        minutes = int(context.args[0])
        if minutes <= 0:
            raise ValueError

        cursor.execute("INSERT OR IGNORE INTO users (chat_id) VALUES (?)", (chat_id,))
        cursor.execute("UPDATE users SET reminder_interval=? WHERE chat_id=?", (minutes, chat_id))
        conn.commit()

        try:
            scheduler.remove_job(str(chat_id))
        except:
            pass

        scheduler.add_job(
            send_reminder, "interval", minutes=minutes,
            args=[context.bot, chat_id], id=str(chat_id)
        )

        await update.message.reply_text(
            f"✅ Reminder set to every *{minutes} minutes*!", parse_mode="Markdown"
        )

    except:
        await update.message.reply_text("❌ Please provide a valid number. Example: /set 30")

# ==========================================
# /goal COMMAND
# ==========================================

async def set_goal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id

    if not context.args:
        await update.message.reply_text("Usage: /goal `8` (glasses per day)", parse_mode="Markdown")
        return

    try:
        goal = int(context.args[0])
        if goal <= 0:
            raise ValueError

        cursor.execute("INSERT OR IGNORE INTO users (chat_id) VALUES (?)", (chat_id,))
        cursor.execute("UPDATE users SET daily_goal=? WHERE chat_id=?", (goal, chat_id))
        conn.commit()

        await update.message.reply_text(
            f"🎯 Daily goal updated to *{goal} glasses*!", parse_mode="Markdown"
        )
    except:
        await update.message.reply_text("❌ Please provide a valid number. Example: /goal 10")

# ==========================================
# /log COMMAND
# ==========================================

async def log_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    glasses = 1
    if context.args:
        try:
            glasses = max(1, int(context.args[0]))
        except:
            pass

    cursor.execute("INSERT OR IGNORE INTO users (chat_id) VALUES (?)", (chat_id,))
    log_water_glass(chat_id, glasses)

    user = get_user(chat_id)
    goal = user[2]
    drunk = glasses_today(chat_id)
    bar = progress_bar(drunk, goal)

    msg = (
        f"💧 Logged *{glasses}* glass{'es' if glasses > 1 else ''}!\n\n"
        f"{bar}\n"
        f"*{drunk}/{goal}* glasses today"
    )
    if drunk >= goal:
        msg += "\n\n🎉 *Goal reached! Amazing work!*"

    await update.message.reply_text(msg, parse_mode="Markdown")

# ==========================================
# /stats COMMAND
# ==========================================

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    cursor.execute("INSERT OR IGNORE INTO users (chat_id) VALUES (?)", (chat_id,))
    user = get_user(chat_id)
    goal = user[2]
    interval = user[1]

    drunk = glasses_today(chat_id)
    remaining = max(0, goal - drunk)
    bar = progress_bar(drunk, goal)
    current_streak, longest_streak = get_streak(chat_id)

    status = "🎉 Goal reached!" if drunk >= goal else f"💪 Keep going! {remaining} more to go"

    await update.message.reply_text(
        f"📊 *Today's Stats*\n\n"
        f"{bar}\n"
        f"💧 Drank: *{drunk}* glasses\n"
        f"🎯 Goal: *{goal}* glasses\n"
        f"⏰ Reminder: every *{interval}* mins\n\n"
        f"{status}\n\n"
        f"🔥 Current streak: *{current_streak}* day{'s' if current_streak != 1 else ''}\n"
        f"🏆 Best streak: *{longest_streak}* day{'s' if longest_streak != 1 else ''}",
        parse_mode="Markdown"
    )

# ==========================================
# /weekly COMMAND
# ==========================================

async def weekly(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    cursor.execute("INSERT OR IGNORE INTO users (chat_id) VALUES (?)", (chat_id,))
    user = get_user(chat_id)
    goal = user[2]

    cursor.execute("""
        SELECT date(logged_at), SUM(glasses)
        FROM water_log
        WHERE chat_id=? AND logged_at >= datetime('now', '-6 days')
        GROUP BY date(logged_at)
        ORDER BY date(logged_at) ASC
    """, (chat_id,))
    rows = cursor.fetchall()

    day_map = {r[0]: r[1] for r in rows}
    total = sum(day_map.values())
    days_goal_met = sum(1 for v in day_map.values() if v >= goal)

    lines = ["📅 *Weekly Report (Last 7 Days)*\n"]
    for i in range(6, -1, -1):
        day = (datetime.datetime.utcnow() - datetime.timedelta(days=i)).strftime("%Y-%m-%d")
        label = (datetime.datetime.utcnow() - datetime.timedelta(days=i)).strftime("%a %d %b")
        drunk = day_map.get(day, 0)
        bar = progress_bar(drunk, goal, length=6)
        tick = "✅" if drunk >= goal else ("🔵" if drunk > 0 else "⬜")
        lines.append(f"{tick} {label}: {bar} *{drunk}*💧")

    lines.append(f"\n📊 Total this week: *{total}* glasses")
    lines.append(f"🏅 Goals met: *{days_goal_met}/7* days")
    avg = round(total / 7, 1)
    lines.append(f"📈 Daily average: *{avg}* glasses")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

# ==========================================
# /streak COMMAND
# ==========================================

async def streak_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    current, longest = get_streak(chat_id)

    emoji = "🔥" if current >= 3 else "💧"
    await update.message.reply_text(
        f"{emoji} *Your Streaks*\n\n"
        f"🔥 Current streak: *{current}* day{'s' if current != 1 else ''}\n"
        f"🏆 Longest streak: *{longest}* day{'s' if longest != 1 else ''}\n\n"
        f"{'Keep it up! You are on fire!' if current >= 3 else 'Hit your daily goal to build your streak! 💪'}",
        parse_mode="Markdown"
    )

# ==========================================
# /stop COMMAND
# ==========================================

async def stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    cursor.execute("UPDATE users SET active=0 WHERE chat_id=?", (chat_id,))
    conn.commit()

    try:
        scheduler.remove_job(str(chat_id))
    except:
        pass

    await update.message.reply_text(
        "⏸ Reminders paused. Use /start to resume anytime! 💧"
    )

# ==========================================
# /help COMMAND
# ==========================================

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "💧 *Water Reminder Bot — Commands*\n\n"
        "*/start* — Start / resume reminders\n"
        "*/stop* — Pause reminders\n"
        "*/set `<mins>`* — Set reminder interval (e.g. /set 45)\n"
        "*/goal `<n>`* — Set daily glass goal (e.g. /goal 10)\n"
        "*/log `[n]`* — Log water (e.g. /log 2 for 2 glasses)\n"
        "*/stats* — Today's progress\n"
        "*/weekly* — Last 7 days report\n"
        "*/streak* — View your streaks\n"
        "*/help* — Show this menu",
        parse_mode="Markdown"
    )

# ==========================================
# CALLBACK QUERY HANDLER (inline buttons)
# ==========================================

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat_id
    data = query.data

    cursor.execute("INSERT OR IGNORE INTO users (chat_id) VALUES (?)", (chat_id,))
    user = get_user(chat_id)
    goal = user[2]

    if data.startswith("log_"):
        glasses = int(data.split("_")[1])
        log_water_glass(chat_id, glasses)
        drunk = glasses_today(chat_id)
        bar = progress_bar(drunk, goal)
        msg = (
            f"💧 Logged *{glasses}* glass{'es' if glasses > 1 else ''}!\n\n"
            f"{bar}\n*{drunk}/{goal}* glasses today"
        )
        if drunk >= goal:
            msg += "\n\n🎉 *Goal reached! Amazing!*"
        await query.edit_message_text(msg, parse_mode="Markdown")

    elif data == "stats_today":
        drunk = glasses_today(chat_id)
        remaining = max(0, goal - drunk)
        bar = progress_bar(drunk, goal)
        current_streak, longest_streak = get_streak(chat_id)
        status = "🎉 Goal reached!" if drunk >= goal else f"💪 {remaining} more to go"
        await query.edit_message_text(
            f"📊 *Today's Stats*\n\n"
            f"{bar}\n"
            f"💧 Drank: *{drunk}/{goal}* glasses\n"
            f"{status}\n\n"
            f"🔥 Streak: *{current_streak}* days | 🏆 Best: *{longest_streak}*",
            parse_mode="Markdown"
        )

    elif data == "streaks":
        current, longest = get_streak(chat_id)
        emoji = "🔥" if current >= 3 else "💧"
        await query.edit_message_text(
            f"{emoji} *Your Streaks*\n\n"
            f"🔥 Current: *{current}* days\n"
            f"🏆 Best: *{longest}* days",
            parse_mode="Markdown"
        )

    elif data == "settings":
        interval = user[1]
        await query.edit_message_text(
            f"⚙️ *Your Settings*\n\n"
            f"⏰ Reminder every: *{interval} mins*\n"
            f"🎯 Daily goal: *{goal} glasses*\n\n"
            f"Change with:\n/set `<mins>` — e.g. /set 45\n/goal `<n>` — e.g. /goal 10",
            parse_mode="Markdown"
        )

# ==========================================
# LOAD ACTIVE USERS ON RESTART
# ==========================================

def load_users(bot):
    cursor.execute("SELECT chat_id, reminder_interval FROM users WHERE active=1")
    users = cursor.fetchall()
    for chat_id, interval in users:
        try:
            scheduler.add_job(
                send_reminder, "interval", minutes=interval,
                args=[bot, chat_id], id=str(chat_id)
            )
        except:
            pass

# ==========================================
# MAIN
# ==========================================

def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("set", set_timer))
    app.add_handler(CommandHandler("goal", set_goal))
    app.add_handler(CommandHandler("log", log_command))
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(CommandHandler("weekly", weekly))
    app.add_handler(CommandHandler("streak", streak_command))
    app.add_handler(CommandHandler("stop", stop))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CallbackQueryHandler(button_handler))

    scheduler.start()
    load_users(app.bot)

    print("🤖 Water Bot Running...")
    app.run_polling()

if __name__ == "__main__":
    main()
