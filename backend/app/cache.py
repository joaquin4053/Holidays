"""SQLite cache for research results, so repeated trips don't cost API credits."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parents[2] / ".tmp" / "research_cache.sqlite"


def _conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("CREATE TABLE IF NOT EXISTS research (key TEXT PRIMARY KEY, value TEXT, created TEXT DEFAULT CURRENT_TIMESTAMP)")
    return conn


def make_key(*parts: object) -> str:
    raw = json.dumps(parts, sort_keys=True, default=str).lower()
    return hashlib.sha256(raw.encode()).hexdigest()


def get(key: str) -> str | None:
    with _conn() as conn:
        row = conn.execute("SELECT value FROM research WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None


def put(key: str, value: str) -> None:
    with _conn() as conn:
        conn.execute("INSERT OR REPLACE INTO research (key, value) VALUES (?, ?)", (key, value))
