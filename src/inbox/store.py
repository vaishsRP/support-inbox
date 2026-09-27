"""SQLite storage. One database file per firm."""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id          TEXT PRIMARY KEY,
    thread_id   TEXT NOT NULL,
    parent_id   TEXT,
    author      TEXT NOT NULL,
    inbound     INTEGER NOT NULL,          -- 1 = from a customer, 0 = from the firm
    created_at  TEXT NOT NULL,             -- ISO 8601, UTC
    text        TEXT NOT NULL              -- raw text as received
);
CREATE INDEX IF NOT EXISTS messages_thread ON messages(thread_id, created_at);
CREATE INDEX IF NOT EXISTS messages_author ON messages(author, created_at);

-- A customer message and the reply the firm actually sent: one approved answer.
CREATE TABLE IF NOT EXISTS pairs (
    id              INTEGER PRIMARY KEY,
    thread_id       TEXT NOT NULL,
    customer_msg_id TEXT NOT NULL UNIQUE,
    customer_id     TEXT NOT NULL,
    asked_at        TEXT NOT NULL,
    answered_at     TEXT NOT NULL,
    question        TEXT NOT NULL,         -- cleaned customer text
    answer          TEXT NOT NULL,         -- cleaned, personal details redacted
    answer_raw      TEXT NOT NULL,
    is_followup     INTEGER NOT NULL,      -- customer message was itself a reply
    is_deflection   INTEGER NOT NULL,      -- "DM us" / contact link, not an answer
    retired         INTEGER NOT NULL DEFAULT 0,
    source          TEXT NOT NULL DEFAULT 'corpus'   -- corpus | sent
);
CREATE INDEX IF NOT EXISTS pairs_asked ON pairs(asked_at);
"""


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    return conn
