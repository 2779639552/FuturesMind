"""AgentSense Database Layer — SQLite persistence for posts, sentiment, alerts, users.

Usage:
    from database import get_db
    db = get_db()
    db.insert_posts(posts)
    stats = db.get_collection_stats()
"""

import sqlite3
import json
import os
import hashlib
import threading
from pathlib import Path
from datetime import datetime, timedelta
from contextlib import contextmanager

DB_DIR = Path(os.path.expanduser("~/.tradingagents"))
DB_PATH = DB_DIR / "agentsense.db"

# ── Singleton connection with thread-local ──────────────────────────────

_local = threading.local()


def get_db() -> "AgentSenseDB":
    if not hasattr(_local, "db"):
        _local.db = AgentSenseDB()
    return _local.db


class AgentSenseDB:
    def __init__(self, path=None):
        self.path = str(path or DB_PATH)
        self.path_obj = Path(self.path)
        self.path_obj.parent.mkdir(parents=True, exist_ok=True)
        self._init_tables()

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_tables(self):
        with self._conn() as c:
            c.executescript("""
                CREATE TABLE IF NOT EXISTS posts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    note_id TEXT UNIQUE NOT NULL,
                    platform TEXT NOT NULL DEFAULT '?',
                    author_name TEXT DEFAULT '',
                    author_fans REAL DEFAULT 0,
                    title TEXT DEFAULT '',
                    content TEXT DEFAULT '',
                    sentiment TEXT DEFAULT 'neutral',
                    sentiment_score REAL DEFAULT 0,
                    publish_time TEXT DEFAULT '',
                    url TEXT DEFAULT '',
                    likes INTEGER DEFAULT 0,
                    comments INTEGER DEFAULT 0,
                    shares INTEGER DEFAULT 0,
                    varieties TEXT DEFAULT '[]',
                    created_at TEXT DEFAULT (datetime('now'))
                );
                CREATE INDEX IF NOT EXISTS idx_posts_platform ON posts(platform);
                CREATE INDEX IF NOT EXISTS idx_posts_publish_time ON posts(publish_time);
                CREATE INDEX IF NOT EXISTS idx_posts_sentiment ON posts(sentiment);

                CREATE TABLE IF NOT EXISTS sentiment_daily (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    variety TEXT NOT NULL,
                    date TEXT NOT NULL,
                    simple_avg REAL DEFAULT 0,
                    avg_score REAL DEFAULT 0,
                    bullish_ratio REAL DEFAULT 0,
                    bearish_ratio REAL DEFAULT 0,
                    neutral_ratio REAL DEFAULT 0,
                    total_notes INTEGER DEFAULT 0,
                    author_count INTEGER DEFAULT 0,
                    platform_breakdown TEXT DEFAULT '{}',
                    updated_at TEXT DEFAULT (datetime('now')),
                    UNIQUE(variety, date)
                );
                CREATE INDEX IF NOT EXISTS idx_sent_variety_date ON sentiment_daily(variety, date);

                CREATE TABLE IF NOT EXISTS alerts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    alert_type TEXT NOT NULL DEFAULT 'info',
                    variety TEXT DEFAULT '',
                    title TEXT NOT NULL,
                    message TEXT DEFAULT '',
                    severity TEXT DEFAULT 'info',
                    data TEXT DEFAULT '{}',
                    acknowledged INTEGER DEFAULT 0,
                    created_at TEXT DEFAULT (datetime('now'))
                );
                CREATE INDEX IF NOT EXISTS idx_alerts_created ON alerts(created_at);

                CREATE TABLE IF NOT EXISTS collection_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    platform TEXT NOT NULL,
                    keywords_count INTEGER DEFAULT 0,
                    posts_collected INTEGER DEFAULT 0,
                    posts_after_filter INTEGER DEFAULT 0,
                    status TEXT DEFAULT 'running',
                    error_msg TEXT DEFAULT '',
                    started_at TEXT DEFAULT (datetime('now')),
                    finished_at TEXT DEFAULT ''
                );

                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    is_admin INTEGER DEFAULT 0,
                    created_at TEXT DEFAULT (datetime('now'))
                );

                CREATE TABLE IF NOT EXISTS watchlist (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER DEFAULT 1,
                    variety TEXT NOT NULL,
                    added_at TEXT DEFAULT (datetime('now')),
                    UNIQUE(user_id, variety)
                );

                CREATE TABLE IF NOT EXISTS trade_signals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    variety TEXT NOT NULL,
                    signal_date TEXT NOT NULL,
                    signal_type TEXT NOT NULL DEFAULT 'author_weighted',
                    signal_value REAL DEFAULT 0,
                    direction TEXT DEFAULT 'hold',
                    entry_price REAL,
                    exit_price REAL,
                    horizon_days INTEGER DEFAULT 3,
                    pnl_pct REAL,
                    outcome TEXT DEFAULT 'pending',
                    created_at TEXT DEFAULT (datetime('now'))
                );
                CREATE INDEX IF NOT EXISTS idx_trade_variety ON trade_signals(variety);
                CREATE INDEX IF NOT EXISTS idx_trade_outcome ON trade_signals(outcome);
            """)

    # ── Posts ──────────────────────────────────────────────────────────

    def insert_posts_batch(self, posts: list[dict]) -> int:
        """Insert or ignore posts. Returns count of new posts inserted."""
        count = 0
        with self._conn() as c:
            for p in posts:
                try:
                    c.execute("""
                        INSERT OR IGNORE INTO posts (note_id, platform, author_name, author_fans,
                            title, content, sentiment, sentiment_score, publish_time, url,
                            likes, comments, shares, varieties)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        p.get("note_id", ""),
                        p.get("platform", "?"),
                        (p.get("author_name", "") or "")[:50],
                        p.get("author_fans", 0) or 0,
                        (p.get("title", "") or "")[:200],
                        p.get("content", "") or "",
                        p.get("sentiment", "neutral"),
                        p.get("sentiment_score", 0) or 0,
                        (p.get("publish_time", "") or "")[:19],
                        p.get("url", "") or "",
                        p.get("like_count", 0) or 0,
                        p.get("comment_count", 0) or 0,
                        p.get("share_count", 0) or 0,
                        json.dumps(p.get("varieties", []), ensure_ascii=False),
                    ))
                    if c.rowcount > 0:
                        count += 1
                except Exception:
                    pass
        return count

    def get_posts(self, platform=None, variety=None, sentiment=None, since=None, limit=200) -> list[dict]:
        conditions = []
        params = []
        if platform:
            conditions.append("platform = ?")
            params.append(platform)
        if variety:
            conditions.append("varieties LIKE ?")
            params.append(f'%"{variety}"%')
        if sentiment:
            conditions.append("sentiment = ?")
            params.append(sentiment)
        if since:
            conditions.append("publish_time >= ?")
            params.append(since)

        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        with self._conn() as c:
            rows = c.execute(
                f"SELECT * FROM posts {where} ORDER BY publish_time DESC LIMIT ?",
                params + [limit]
            ).fetchall()
        return [dict(r) for r in rows]

    def get_platform_stats(self) -> dict:
        with self._conn() as c:
            rows = c.execute("""
                SELECT platform, COUNT(*) as cnt, MIN(publish_time) as earliest, MAX(publish_time) as latest
                FROM posts GROUP BY platform ORDER BY cnt DESC
            """).fetchall()
        result = {}
        for r in rows:
            result[r["platform"]] = {
                "count": r["cnt"],
                "earliest": r["earliest"][:10] if r["earliest"] else "?",
                "latest": r["latest"][:10] if r["latest"] else "?",
            }
        return result

    def get_total_posts(self) -> int:
        with self._conn() as c:
            return c.execute("SELECT COUNT(*) FROM posts").fetchone()[0]

    # ── Sentiment Daily ────────────────────────────────────────────────

    def upsert_sentiment_daily(self, variety: str, date: str, data: dict):
        with self._conn() as c:
            c.execute("""
                INSERT INTO sentiment_daily (variety, date, simple_avg, avg_score,
                    bullish_ratio, bearish_ratio, neutral_ratio, total_notes, author_count, platform_breakdown)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(variety, date) DO UPDATE SET
                    simple_avg=excluded.simple_avg, avg_score=excluded.avg_score,
                    bullish_ratio=excluded.bullish_ratio, bearish_ratio=excluded.bearish_ratio,
                    neutral_ratio=excluded.neutral_ratio, total_notes=excluded.total_notes,
                    author_count=excluded.author_count, platform_breakdown=excluded.platform_breakdown,
                    updated_at=datetime('now')
            """, (
                variety, date,
                data.get("simple_avg", 0), data.get("avg_score", 0),
                data.get("bullish_ratio", 0), data.get("bearish_ratio", 0),
                data.get("neutral_ratio", 0), data.get("total_notes", 0),
                data.get("author_count", 0),
                json.dumps(data.get("platform_breakdown", {}), ensure_ascii=False),
            ))

    def get_sentiment_series(self, variety: str, days: int = 180) -> list[dict]:
        since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM sentiment_daily WHERE variety=? AND date>=? ORDER BY date",
                (variety, since)
            ).fetchall()
        return [dict(r) for r in rows]

    # ── Alerts ─────────────────────────────────────────────────────────

    def create_alert(self, alert_type: str, title: str, message: str = "",
                     variety: str = "", severity: str = "info", data: dict = None) -> int:
        with self._conn() as c:
            c.execute(
                "INSERT INTO alerts (alert_type, variety, title, message, severity, data) VALUES (?, ?, ?, ?, ?, ?)",
                (alert_type, variety, title, message, severity, json.dumps(data or {}, ensure_ascii=False))
            )
            return c.lastrowid

    def get_alerts(self, limit=50, unacknowledged_only=False) -> list[dict]:
        where = "WHERE acknowledged=0" if unacknowledged_only else ""
        with self._conn() as c:
            rows = c.execute(
                f"SELECT * FROM alerts {where} ORDER BY created_at DESC LIMIT ?",
                (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    def acknowledge_alert(self, alert_id: int):
        with self._conn() as c:
            c.execute("UPDATE alerts SET acknowledged=1 WHERE id=?", (alert_id,))

    def get_unacknowledged_count(self) -> int:
        with self._conn() as c:
            return c.execute("SELECT COUNT(*) FROM alerts WHERE acknowledged=0").fetchone()[0]

    # ── Collection Log ─────────────────────────────────────────────────

    def start_collection(self, platform: str, keywords_count: int) -> int:
        with self._conn() as c:
            c.execute(
                "INSERT INTO collection_log (platform, keywords_count, status) VALUES (?, ?, 'running')",
                (platform, keywords_count)
            )
            return c.lastrowid

    def finish_collection(self, log_id: int, posts_collected: int, posts_after_filter: int = 0, error: str = ""):
        status = "error" if error else "success"
        with self._conn() as c:
            c.execute(
                "UPDATE collection_log SET status=?, posts_collected=?, posts_after_filter=?, error_msg=?, finished_at=datetime('now') WHERE id=?",
                (status, posts_collected, posts_after_filter or posts_collected, error, log_id)
            )

    def get_collection_history(self, limit=20) -> list[dict]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM collection_log ORDER BY started_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    # ── Users / Auth ───────────────────────────────────────────────────

    def create_user(self, username: str, password: str, is_admin: bool = False) -> bool:
        pwd_hash = hashlib.sha256(password.encode()).hexdigest()
        try:
            with self._conn() as c:
                c.execute(
                    "INSERT INTO users (username, password_hash, is_admin) VALUES (?, ?, ?)",
                    (username, pwd_hash, 1 if is_admin else 0)
                )
            return True
        except sqlite3.IntegrityError:
            return False

    def verify_user(self, username: str, password: str) -> bool:
        pwd_hash = hashlib.sha256(password.encode()).hexdigest()
        with self._conn() as c:
            row = c.execute(
                "SELECT id FROM users WHERE username=? AND password_hash=?",
                (username, pwd_hash)
            ).fetchone()
        return row is not None

    def ensure_default_user(self):
        """Create default admin if no users exist."""
        with self._conn() as c:
            count = c.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        if count == 0:
            self.create_user("admin", "agentsense2026", is_admin=True)

    # ── Watchlist ──────────────────────────────────────────────────────

    def get_watchlist(self, user_id=1) -> list[str]:
        with self._conn() as c:
            rows = c.execute("SELECT variety FROM watchlist WHERE user_id=?", (user_id,)).fetchall()
        return [r["variety"] for r in rows]

    def add_to_watchlist(self, variety: str, user_id=1):
        with self._conn() as c:
            c.execute("INSERT OR IGNORE INTO watchlist (user_id, variety) VALUES (?, ?)", (user_id, variety))

    def remove_from_watchlist(self, variety: str, user_id=1):
        with self._conn() as c:
            c.execute("DELETE FROM watchlist WHERE user_id=? AND variety=?", (user_id, variety))

    # ── Trade Signals ──────────────────────────────────────────────────

    def save_trade_signal(self, variety: str, date: str, signal_value: float,
                          direction: str, entry_price: float, horizon_days: int = 3):
        with self._conn() as c:
            c.execute("""
                INSERT INTO trade_signals (variety, signal_date, signal_value, direction, entry_price, horizon_days)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (variety, date, signal_value, direction, entry_price, horizon_days))

    def resolve_trade_signal(self, signal_id: int, exit_price: float, pnl_pct: float, outcome: str):
        with self._conn() as c:
            c.execute(
                "UPDATE trade_signals SET exit_price=?, pnl_pct=?, outcome=? WHERE id=?",
                (exit_price, pnl_pct, outcome, signal_id)
            )

    def get_trade_signals(self, variety=None, outcome=None, limit=100) -> list[dict]:
        conditions = []
        params = []
        if variety:
            conditions.append("variety = ?")
            params.append(variety)
        if outcome:
            conditions.append("outcome = ?")
            params.append(outcome)
        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        with self._conn() as c:
            rows = c.execute(
                f"SELECT * FROM trade_signals {where} ORDER BY created_at DESC LIMIT ?",
                params + [limit]
            ).fetchall()
        return [dict(r) for r in rows]

    def get_trade_stats(self) -> dict:
        with self._conn() as c:
            total = c.execute("SELECT COUNT(*) FROM trade_signals WHERE outcome != 'pending'").fetchone()[0]
            wins = c.execute("SELECT COUNT(*) FROM trade_signals WHERE outcome='win'").fetchone()[0]
            avg_pnl = c.execute("SELECT AVG(pnl_pct) FROM trade_signals WHERE outcome != 'pending'").fetchone()[0]
        return {
            "total_trades": total,
            "wins": wins,
            "losses": total - wins,
            "win_rate": round(wins / total, 3) if total > 0 else 0,
            "avg_pnl_pct": round(avg_pnl or 0, 3),
        }
