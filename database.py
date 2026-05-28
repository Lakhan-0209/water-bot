import sqlite3
import os
from datetime import datetime, date

DB_PATH = os.environ.get("DB_PATH", "water.db")


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                user_id     INTEGER PRIMARY KEY,
                username    TEXT,
                first_name  TEXT,
                timezone    TEXT    DEFAULT 'Asia/Kolkata',
                daily_goal  INTEGER DEFAULT 2500,
                reminder_interval INTEGER DEFAULT 60,
                reminder_start TEXT DEFAULT '08:00',
                reminder_end   TEXT DEFAULT '22:00',
                active      INTEGER DEFAULT 1,
                created_at  TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS water_log (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     INTEGER NOT NULL,
                amount_ml   INTEGER NOT NULL,
                logged_at   TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            );

            CREATE TABLE IF NOT EXISTS reminder_schedule (
                user_id     INTEGER PRIMARY KEY,
                next_remind TEXT,
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            );
        """)


# ── User helpers ──────────────────────────────────────────────────────────────

def upsert_user(user_id, username, first_name):
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO users (user_id, username, first_name)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                username   = excluded.username,
                first_name = excluded.first_name
        """, (user_id, username, first_name))


def get_user(user_id):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()
        return dict(row) if row else None


def set_user_field(user_id, field, value):
    allowed = {
        "timezone", "daily_goal", "reminder_interval",
        "reminder_start", "reminder_end", "active"
    }
    if field not in allowed:
        raise ValueError(f"Unknown field: {field}")
    with get_conn() as conn:
        conn.execute(
            f"UPDATE users SET {field} = ? WHERE user_id = ?",
            (value, user_id)
        )


def get_all_active_users():
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM users WHERE active = 1"
        ).fetchall()
        return [dict(r) for r in rows]


# ── Water log helpers ─────────────────────────────────────────────────────────

def log_water(user_id, amount_ml):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO water_log (user_id, amount_ml) VALUES (?, ?)",
            (user_id, amount_ml)
        )


def get_today_total(user_id):
    with get_conn() as conn:
        row = conn.execute("""
            SELECT COALESCE(SUM(amount_ml), 0) AS total
            FROM water_log
            WHERE user_id = ?
              AND date(logged_at) = date('now')
        """, (user_id,)).fetchone()
        return row["total"] if row else 0


def get_week_stats(user_id):
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT date(logged_at) AS day,
                   SUM(amount_ml)  AS total
            FROM water_log
            WHERE user_id = ?
              AND logged_at >= date('now', '-6 days')
            GROUP BY day
            ORDER BY day
        """, (user_id,)).fetchall()
        return [dict(r) for r in rows]


def get_month_stats(user_id):
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT date(logged_at) AS day,
                   SUM(amount_ml)  AS total
            FROM water_log
            WHERE user_id = ?
              AND strftime('%Y-%m', logged_at) = strftime('%Y-%m', 'now')
            GROUP BY day
            ORDER BY day
        """, (user_id,)).fetchall()
        return [dict(r) for r in rows]


def get_streak(user_id, daily_goal):
    """Return current consecutive days the user met their goal."""
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT date(logged_at) AS day,
                   SUM(amount_ml)  AS total
            FROM water_log
            WHERE user_id = ?
            GROUP BY day
            ORDER BY day DESC
        """, (user_id,)).fetchall()

    streak = 0
    today = date.today()
    for i, row in enumerate(rows):
        expected = today.toordinal() - i
        actual   = date.fromisoformat(row["day"]).toordinal()
        if actual != expected:
            break
        if row["total"] >= daily_goal:
            streak += 1
        else:
            break
    return streak
