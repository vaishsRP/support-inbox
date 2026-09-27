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

-- Every mail the tool handled: what it drafted (or why it did not), and later what
-- the human actually sent.
CREATE TABLE IF NOT EXISTS drafts (
    id              INTEGER PRIMARY KEY,
    thread_id       TEXT NOT NULL,
    customer_msg_id TEXT NOT NULL,
    customer_id     TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    route           TEXT NOT NULL,         -- reuse | docs | refused | blocked | failed
    confidence      TEXT,                  -- high | medium | low, null when no draft
    reason          TEXT,                  -- why refused/blocked/failed, or agent notes
    draft_text      TEXT,
    source_pair_id  INTEGER,
    sources         TEXT,                  -- JSON: what the draft was built from
    sent_text       TEXT,
    sent_at         TEXT,
    edit_similarity REAL,                  -- 1 - normalised edit distance, plain text
    outcome         TEXT NOT NULL DEFAULT 'pending',  -- pending | sent_as_is | edited | discarded | no_draft
    run             TEXT                   -- null for live mail; a replay run's name otherwise
);
CREATE INDEX IF NOT EXISTS drafts_thread ON drafts(thread_id);

-- The action list. One table, three kinds of row, open or done and nothing more.
CREATE TABLE IF NOT EXISTS actions (
    id          INTEGER PRIMARY KEY,
    kind        TEXT NOT NULL CHECK (kind IN ('commitment', 'check', 'spike')),
    status      TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'done')),
    what        TEXT NOT NULL,
    customer_id TEXT,
    thread_id   TEXT,
    due_at      TEXT,                      -- null = no deadline could be read
    due_text    TEXT,                      -- the words the deadline came from, or why not
    rule        TEXT,
    origin      TEXT,                      -- draft | sent | spike
    created_at  TEXT NOT NULL,
    done_at     TEXT
);
CREATE INDEX IF NOT EXISTS actions_open ON actions(status, due_at);

-- Standing documents and dated notes from the context page, chunked for retrieval.
CREATE TABLE IF NOT EXISTS docs (
    id          INTEGER PRIMARY KEY,
    kind        TEXT NOT NULL CHECK (kind IN ('standing', 'note')),
    title       TEXT NOT NULL,
    body        TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    expires_at  TEXT,                      -- notes only; null for standing documents
    retired     INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS doc_chunks (
    id      INTEGER PRIMARY KEY,
    doc_id  INTEGER NOT NULL REFERENCES docs(id) ON DELETE CASCADE,
    text    TEXT NOT NULL
);
"""


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    _migrate(conn)
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    """Columns added after a database was first created."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(drafts)")}
    if "run" not in cols:
        conn.execute("ALTER TABLE drafts ADD COLUMN run TEXT")
        conn.commit()
