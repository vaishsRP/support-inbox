"""Standing documents and dated notes: the context page's store.

Standing documents (policies, help centre, product docs) never expire. Dated
notes ("v2 shipped Tuesday, login changed, here's the workaround") expire, so a
three-month-old incident note stops surfacing.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import numpy as np

from .embed import encode

CHUNK_CHARS = 700
DEFAULT_NOTE_DAYS = 14


@dataclass
class DocHit:
    doc_id: int
    chunk_id: int
    kind: str
    title: str
    text: str
    created_at: str
    sim: float


def chunk(body: str, size: int = CHUNK_CHARS) -> list[str]:
    """Split on paragraphs, then pack paragraphs into chunks of about `size` chars."""
    paras = [p.strip() for p in re.split(r"\n\s*\n", body or "") if p.strip()]
    out, cur = [], ""
    for p in paras:
        while len(p) > size:
            cut = p.rfind(". ", 0, size)
            cut = cut + 1 if cut > size // 3 else size
            if cur:
                out.append(cur)
                cur = ""
            out.append(p[:cut].strip())
            p = p[cut:].strip()
        if cur and len(cur) + len(p) + 2 > size:
            out.append(cur)
            cur = p
        else:
            cur = f"{cur}\n\n{p}" if cur else p
    if cur:
        out.append(cur)
    return out


def add_doc(
    conn: sqlite3.Connection,
    kind: str,
    title: str,
    body: str,
    *,
    created_at: datetime | None = None,
    expires_in_days: int | None = None,
    owner: str | None = None,
) -> int:
    if kind not in ("standing", "note"):
        raise ValueError("kind must be 'standing' or 'note'")
    created = created_at or datetime.now(timezone.utc)
    expires = None
    if kind == "note":
        expires = created + timedelta(days=expires_in_days or DEFAULT_NOTE_DAYS)
    cur = conn.execute(
        "INSERT INTO docs (kind, title, body, created_at, expires_at, owner) VALUES (?,?,?,?,?,?)",
        (kind, title.strip(), body.strip(), created.isoformat(), expires.isoformat() if expires else None, owner),
    )
    doc_id = cur.lastrowid
    conn.executemany(
        "INSERT INTO doc_chunks (doc_id, text) VALUES (?,?)", [(doc_id, c) for c in chunk(body)]
    )
    return doc_id


def retire_doc(conn: sqlite3.Connection, doc_id: int) -> None:
    conn.execute("UPDATE docs SET retired = 1 WHERE id = ?", (doc_id,))


def live_docs(conn: sqlite3.Connection, at: datetime | None = None) -> list[sqlite3.Row]:
    at_s = (at or datetime.now(timezone.utc)).isoformat()
    return conn.execute(
        "SELECT * FROM docs WHERE retired = 0 AND created_at <= ? AND (expires_at IS NULL OR expires_at > ?) "
        "ORDER BY kind DESC, created_at DESC",
        (at_s, at_s),
    ).fetchall()


class DocIndex:
    """Chunks of live documents at a moment in time. Small; rebuilt on demand."""

    def __init__(self, conn: sqlite3.Connection, at: datetime | None = None):
        self.at = at or datetime.now(timezone.utc)
        at_s = self.at.isoformat()
        self.rows = conn.execute(
            """SELECT c.id AS chunk_id, c.text, d.id AS doc_id, d.kind, d.title, d.created_at, d.owner
               FROM doc_chunks c JOIN docs d ON d.id = c.doc_id
               WHERE d.retired = 0 AND d.created_at <= ? AND (d.expires_at IS NULL OR d.expires_at > ?)""",
            (at_s, at_s),
        ).fetchall()
        texts = [f"{r['title']}. {r['text']}" for r in self.rows]
        self.vecs = encode(texts) if texts else np.zeros((0, 384), dtype=np.float32)

    def __len__(self) -> int:
        return len(self.rows)

    def search(
        self, text: str | None = None, vec: np.ndarray | None = None, k: int = 3, owner: str | None = None
    ) -> list[DocHit]:
        """Documents for the whole team, plus notes owned by `owner` (the public demo
        keeps each visitor's notes to themselves)."""
        if not len(self.rows):
            return []
        if vec is None:
            vec = encode([text])[0]
        visible = np.array([r["owner"] is None or r["owner"] == owner for r in self.rows])
        sims = np.where(visible, self.vecs @ vec, -np.inf)
        out = []
        for i in np.argsort(-sims)[:k]:
            if not np.isfinite(sims[i]):
                break
            r = self.rows[i]
            out.append(DocHit(r["doc_id"], r["chunk_id"], r["kind"], r["title"], r["text"], r["created_at"], float(sims[i])))
        return out
