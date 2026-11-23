# -*- coding: utf-8 -*-
"""لایه دیتابیس (SQLite). هر عملیات اتصال کوتاه خودش را باز می‌کند تا بین
نخ‌های FastAPI و نخ worker مشکلی پیش نیاید (WAL فعال است)."""

import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .defaults import DEFAULT_EXCEPTIONS, DEFAULT_SETTINGS, HARD_BAD, NEGATABLE_BAD

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("DIVAR_DATA_DIR", str(BASE_DIR / "data")))
DB_PATH = DATA_DIR / "divar.db"
LOCAL_TZ = timezone(timedelta(minutes=210))  # ساعت ایران (UTC+3:30)

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS words (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    word    TEXT NOT NULL,
    kind    TEXT NOT NULL DEFAULT 'hard',      -- hard | negatable
    pattern TEXT NOT NULL DEFAULT '',
    enabled INTEGER NOT NULL DEFAULT 1,
    hits    INTEGER NOT NULL DEFAULT 0,
    UNIQUE(word, kind)
);
CREATE TABLE IF NOT EXISTS exceptions (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    phrase     TEXT NOT NULL UNIQUE,
    word       TEXT NOT NULL DEFAULT '',   -- واژهٔ ممنوعه‌ای که استثنا برایش است
    enabled    INTEGER NOT NULL DEFAULT 1,
    hits       INTEGER NOT NULL DEFAULT 0,
    created_at TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS ads (
    token      TEXT PRIMARY KEY,
    title      TEXT DEFAULT '',
    url        TEXT DEFAULT '',
    price      INTEGER,
    mileage    INTEGER,
    year       INTEGER,
    status     TEXT NOT NULL DEFAULT 'new',    -- sent | rejected | waiting | error
    reason     TEXT DEFAULT '',
    detail_json TEXT DEFAULT '',
    first_seen TEXT DEFAULT '',
    checked_at TEXT DEFAULT '',
    sent_at    TEXT DEFAULT '',
    tries      INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS pending (
    token      TEXT PRIMARY KEY,
    title      TEXT DEFAULT '',
    text       TEXT NOT NULL,
    tries      INTEGER NOT NULL DEFAULT 0,
    added_at   TEXT DEFAULT '',
    last_error TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS runs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT DEFAULT '',
    finished_at TEXT DEFAULT '',
    ok         INTEGER NOT NULL DEFAULT 1,
    fetched    INTEGER NOT NULL DEFAULT 0,
    new_ads    INTEGER NOT NULL DEFAULT 0,
    sent       INTEGER NOT NULL DEFAULT 0,
    rejected   INTEGER NOT NULL DEFAULT 0,
    message    TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS settings_history (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    changed_at    TEXT DEFAULT '',
    summary       TEXT DEFAULT '',
    payload       TEXT DEFAULT '',
    preview_count INTEGER
);
CREATE INDEX IF NOT EXISTS idx_ads_status ON ads(status);
CREATE INDEX IF NOT EXISTS idx_ads_first_seen ON ads(first_seen);
CREATE INDEX IF NOT EXISTS idx_ads_sent_at ON ads(sent_at);
"""


# ---------------------------------------------------------------- کمکی‌ها
def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def query(sql: str, params=()):
    with connect() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def query_one(sql: str, params=()):
    rows = query(sql, params)
    return rows[0] if rows else None


def execute(sql: str, params=()):
    with connect() as conn:
        cur = conn.execute(sql, params)
        conn.commit()
        return cur.lastrowid


def init_db() -> None:
    """ساخت جدول‌ها و پر کردن مقادیر اولیه (فقط اگر خالی باشند)."""
    with connect() as conn:
        conn.executescript(SCHEMA)
        have = conn.execute("SELECT COUNT(*) AS n FROM settings").fetchone()["n"]
        if not have:
            conn.executemany(
                "INSERT OR IGNORE INTO settings(key, value) VALUES(?, ?)",
                list(DEFAULT_SETTINGS.items()),
            )
        # تنظیمات جدیدی که در نسخه‌های بعد اضافه شده‌اند
        for key, value in DEFAULT_SETTINGS.items():
            conn.execute(
                "INSERT OR IGNORE INTO settings(key, value) VALUES(?, ?)", (key, value)
            )
        n_words = conn.execute("SELECT COUNT(*) AS n FROM words").fetchone()["n"]
        if not n_words:
            rows = [(label, "hard", pat) for label, pat in HARD_BAD]
            rows += [(label, "negatable", pat) for label, pat in NEGATABLE_BAD]
            conn.executemany(
                "INSERT OR IGNORE INTO words(word, kind, pattern) VALUES(?, ?, ?)", rows
            )
        cols = [r[1] for r in conn.execute("PRAGMA table_info(exceptions)")]
        if "word" not in cols:
            conn.execute(
                "ALTER TABLE exceptions ADD COLUMN word TEXT NOT NULL DEFAULT ''"
            )
        conn.executemany(
            "INSERT OR IGNORE INTO exceptions(phrase, word, created_at) VALUES(?, ?, ?)",
            [(phrase, word, now_iso()) for word, phrase in DEFAULT_EXCEPTIONS],
        )
        conn.commit()


# ---------------------------------------------------------------- تنظیمات
INT_KEYS = {
    "price_min",
    "price_max",
    "mileage_min",
    "mileage_max",
    "year_min",
    "year_max",
    "max_pages",
    "fetch_back_hours",
    "interval_minutes",
    "hourly_cap",
    "daily_cap",
    "send_from_hour",
    "send_to_hour",
    "price_vs_market",
}


def get_settings() -> dict:
    """همه تنظیمات به‌صورت رشته (همان چیزی که در پایگاه داده است)."""
    rows = query("SELECT key, value FROM settings")
    data = {r["key"]: r["value"] for r in rows}
    for key, value in DEFAULT_SETTINGS.items():
        data.setdefault(key, value)
    return data


def typed_settings() -> dict:
    """تنظیمات با اعداد تبدیل‌شده، آماده برای استفاده در فیلتر و ربات."""
    data = get_settings()
    out = dict(data)
    for key in INT_KEYS:
        try:
            out[key] = int(str(data.get(key, "0")).strip() or 0)
        except (TypeError, ValueError):
            out[key] = int(DEFAULT_SETTINGS.get(key, "0"))
    out["enabled"] = str(data.get("enabled", "1")) not in ("0", "false", "False", "")
    out["chat_id"] = str(data.get("chat_id", "")).strip()
    out["allow_front_impact"] = str(data.get("allow_front_impact", "0")) not in (
        "0",
        "false",
        "False",
        "",
    )
    return out


def set_settings(values: dict) -> None:
    if not values:
        return
    with connect() as conn:
        conn.executemany(
            "INSERT INTO settings(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            [(str(k), str(v)) for k, v in values.items()],
        )
        conn.commit()


def get_setting(key: str, default: str = "") -> str:
    row = query_one("SELECT value FROM settings WHERE key = ?", (key,))
    return row["value"] if row else default


def log_settings_change(summary: str, payload: dict, preview_count) -> None:
    execute(
        "INSERT INTO settings_history(changed_at, summary, payload, preview_count) "
        "VALUES(?, ?, ?, ?)",
        (now_iso(), summary, json.dumps(payload, ensure_ascii=False), preview_count),
    )


def settings_history(limit: int = 20):
    return query(
        "SELECT * FROM settings_history ORDER BY id DESC LIMIT ?", (limit,)
    )


# ---------------------------------------------------------------- واژه‌ها
def list_words():
    return query("SELECT * FROM words ORDER BY kind, id")


def add_word(word: str, kind: str = "hard", pattern: str = "") -> None:
    word = (word or "").strip()
    if not word:
        return
    if pattern.strip():
        pat = pattern.strip()
    else:
        # واژه ساده: خودش را به‌عنوان الگو، با مرز کلمه فارسی
        pat = r"(?<![\u0600-\u06FF])" + word.replace(" ", r"\s*") + r"(?![\u0600-\u06FF])"
    kind = "negatable" if kind == "negatable" else "hard"
    execute(
        "INSERT OR IGNORE INTO words(word, kind, pattern, enabled) VALUES(?, ?, ?, 1)",
        (word, kind, pat),
    )


def set_word(word_id: int, enabled: bool | None = None, pattern: str | None = None):
    if enabled is not None:
        execute("UPDATE words SET enabled = ? WHERE id = ?", (1 if enabled else 0, word_id))
    if pattern is not None and pattern.strip():
        execute("UPDATE words SET pattern = ? WHERE id = ?", (pattern.strip(), word_id))


def delete_word(word_id: int) -> None:
    execute("DELETE FROM words WHERE id = ?", (word_id,))


def bump_word_hits(word: str) -> None:
    execute("UPDATE words SET hits = hits + 1 WHERE word = ?", (word,))


# ---------------------------------------------------------------- استثناها
def list_exceptions():
    return query("SELECT * FROM exceptions ORDER BY id")


def add_exception(phrase: str, word: str = "") -> None:
    phrase = " ".join((phrase or "").split())
    word = (word or "").strip()
    if len(phrase) < 2:
        return
    execute(
        "INSERT INTO exceptions(phrase, word, created_at) VALUES(?, ?, ?) "
        "ON CONFLICT(phrase) DO UPDATE SET enabled = 1, word = excluded.word",
        (phrase, word, now_iso()),
    )


def set_exception(exception_id: int, enabled: bool) -> None:
    execute(
        "UPDATE exceptions SET enabled = ? WHERE id = ?",
        (1 if enabled else 0, exception_id),
    )


def delete_exception(exception_id: int) -> None:
    execute("DELETE FROM exceptions WHERE id = ?", (exception_id,))


def bump_exception_hits(phrase: str) -> None:
    execute("UPDATE exceptions SET hits = hits + 1 WHERE phrase = ?", (phrase,))


# ---------------------------------------------------------------- آگهی‌ها
def ad_exists(token: str) -> bool:
    return bool(query_one("SELECT 1 FROM ads WHERE token = ?", (token,)))


def ad_status(token: str):
    row = query_one("SELECT status FROM ads WHERE token = ?", (token,))
    return row["status"] if row else None


def save_ad(ad: dict) -> None:
    execute(
        "INSERT INTO ads(token, title, url, price, mileage, year, status, reason, "
        "detail_json, first_seen, checked_at) VALUES(?,?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(token) DO UPDATE SET title=excluded.title, price=excluded.price, "
        "mileage=excluded.mileage, year=excluded.year, status=excluded.status, "
        "reason=excluded.reason, detail_json=excluded.detail_json, "
        "checked_at=excluded.checked_at",
        (
            ad["token"],
            ad.get("title", ""),
            ad.get("url", ""),
            ad.get("price"),
            ad.get("mileage"),
            ad.get("year"),
            ad.get("status", "rejected"),
            ad.get("reason", ""),
            json.dumps(ad.get("detail") or {}, ensure_ascii=False),
            ad.get("first_seen") or now_iso(),
            now_iso(),
        ),
    )


def mark_sent(token: str) -> None:
    execute(
        "UPDATE ads SET status = 'sent', sent_at = ?, reason = '' WHERE token = ?",
        (now_iso(), token),
    )
    execute("DELETE FROM pending WHERE token = ?", (token,))


def set_ad_status(token: str, status: str, reason: str = "") -> None:
    execute(
        "UPDATE ads SET status = ?, reason = ? WHERE token = ?", (status, reason, token)
    )


def ads_page(status: str = "", limit: int = 200):
    if status:
        return query(
            "SELECT token, title, url, price, mileage, year, status, reason, "
            "first_seen, sent_at, detail_json FROM ads WHERE status = ? "
            "ORDER BY first_seen DESC LIMIT ?",
            (status, limit),
        )
    return query(
        "SELECT token, title, url, price, mileage, year, status, reason, "
        "first_seen, sent_at, detail_json FROM ads ORDER BY first_seen DESC LIMIT ?",
        (limit,),
    )


def market_prices(year, span: int = 1):
    """قیمت آگهی‌های هم‌مدل (سال ±span)، برای سنجش «قیمت مناسب»."""
    if not year:
        return []
    rows = query(
        "SELECT price FROM ads WHERE price IS NOT NULL AND price > 0 "
        "AND year BETWEEN ? AND ?",
        (int(year) - span, int(year) + span),
    )
    return sorted(r["price"] for r in rows)


def reject_reason_counts(limit: int = 8):
    """پرتکرارترین دلیل‌های رد شدن، برای نشان دادن در پنل."""
    return query(
        "SELECT reason, COUNT(*) AS n FROM ads "
        "WHERE status = 'rejected' AND reason != '' "
        "GROUP BY reason ORDER BY n DESC LIMIT ?",
        (limit,),
    )


def status_counts():
    rows = query("SELECT status, COUNT(*) AS n FROM ads GROUP BY status")
    return {r["status"]: r["n"] for r in rows}


def sent_today() -> int:
    start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    row = query_one(
        "SELECT COUNT(*) AS n FROM ads WHERE status = 'sent' AND sent_at >= ?",
        (start.isoformat(timespec="seconds"),),
    )
    return row["n"] if row else 0


def sent_today_local() -> int:
    """تعداد ارسال‌های امروز به وقت ایران (سقف روزانه از نیمه‌شب محلی صفر می‌شود)."""
    start_local = datetime.now(LOCAL_TZ).replace(hour=0, minute=0, second=0, microsecond=0)
    start = start_local.astimezone(timezone.utc).isoformat(timespec="seconds")
    row = query_one(
        "SELECT COUNT(*) AS n FROM ads WHERE status = 'sent' AND sent_at >= ?", (start,)
    )
    return row["n"] if row else 0


def sent_last_hour() -> int:
    since = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(timespec="seconds")
    row = query_one(
        "SELECT COUNT(*) AS n FROM ads WHERE status = 'sent' AND sent_at >= ?", (since,)
    )
    return row["n"] if row else 0


def recent_for_recheck(days: int = 7):
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
    return query(
        "SELECT * FROM ads WHERE detail_json != '' AND first_seen >= ? "
        "AND status IN ('rejected', 'error', 'waiting')",
        (since,),
    )


def expire_waiting_older_than(hours: int = 48) -> int:
    """آگهی‌هایی که زیادی در صف ماندند و دیگر تازه نیستند را کنار می‌گذارد."""
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat(
        timespec="seconds"
    )
    with connect() as conn:
        cur = conn.execute(
            "UPDATE ads SET status = 'rejected', reason = ? "
            "WHERE status = 'waiting' AND first_seen < ?",
            (f"از صف خارج شد (بیشتر از {hours} ساعت در انتظار ماند)", since),
        )
        conn.commit()
        return cur.rowcount


def ads_with_details(days: int = 7):
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
    return query(
        "SELECT * FROM ads WHERE detail_json != '' AND first_seen >= ?", (since,)
    )


# ---------------------------------------------------------------- صف ارسال
def pending_list():
    return query("SELECT * FROM pending ORDER BY added_at")


def add_pending(token: str, title: str, text: str, error: str = "") -> None:
    execute(
        "INSERT INTO pending(token, title, text, tries, added_at, last_error) "
        "VALUES(?,?,?,1,?,?) ON CONFLICT(token) DO UPDATE SET "
        "tries = pending.tries + 1, last_error = excluded.last_error",
        (token, title, text, now_iso(), str(error)[:200]),
    )


def drop_pending(token: str) -> None:
    execute("DELETE FROM pending WHERE token = ?", (token,))


def bump_pending_tries(token: str, error: str = "") -> None:
    execute(
        "UPDATE pending SET tries = tries + 1, last_error = ? WHERE token = ?",
        (str(error)[:200], token),
    )


# ---------------------------------------------------------------- اجراها
def start_run() -> int:
    return execute("INSERT INTO runs(started_at) VALUES(?)", (now_iso(),))


def finish_run(run_id: int, ok: bool, fetched: int, new_ads: int, sent: int,
               rejected: int, message: str = "") -> None:
    execute(
        "UPDATE runs SET finished_at = ?, ok = ?, fetched = ?, new_ads = ?, sent = ?, "
        "rejected = ?, message = ? WHERE id = ?",
        (now_iso(), 1 if ok else 0, fetched, new_ads, sent, rejected, message[:500], run_id),
    )


def recent_runs(limit: int = 50):
    return query("SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,))
