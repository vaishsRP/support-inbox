"""The action list and the morning digest.

Three kinds of row: commitments that went out, checks an agent has to make in a
system the tool cannot see, and spikes. Each is open or done. No assignment, no
priorities, no other statuses: that way lies a project-management tool.
"""

from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timedelta, timezone

from .rules import Match
from .textclean import PLACEHOLDER_RE

_CHECK = re.compile(r"\[\[CHECK:\s*([^\]]+)\]\]")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def add(
    conn: sqlite3.Connection,
    kind: str,
    what: str,
    *,
    customer_id: str | None = None,
    thread_id: str | None = None,
    due_at: datetime | None = None,
    due_text: str | None = None,
    rule: str | None = None,
    origin: str | None = None,
    created_at: datetime | None = None,
) -> int:
    cur = conn.execute(
        """INSERT INTO actions (kind, what, customer_id, thread_id, due_at, due_text, rule, origin, created_at)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (
            kind,
            what,
            customer_id,
            thread_id,
            # Stored in UTC so deadlines compare correctly whatever zone they were read in.
            due_at.astimezone(timezone.utc).isoformat() if due_at else None,
            due_text,
            rule,
            origin,
            (created_at or _now()).isoformat(),
        ),
    )
    return cur.lastrowid


def record_commitments(
    conn: sqlite3.Connection,
    matches: list[Match],
    *,
    customer_id: str,
    thread_id: str,
    origin: str,
    sent_at: datetime | None = None,
) -> list[int]:
    """Tracked commitments from a reply become action rows. Skips exact repeats
    (the same sentence on the same thread), so re-reading a sent folder is safe."""
    ids = []
    for m in matches:
        if m.action != "track":
            continue
        dup = conn.execute(
            "SELECT id FROM actions WHERE kind='commitment' AND thread_id=? AND what=?",
            (thread_id, m.sentence),
        ).fetchone()
        if dup:
            continue
        ids.append(
            add(
                conn,
                "commitment",
                m.sentence,
                customer_id=customer_id,
                thread_id=thread_id,
                due_at=m.due,
                due_text=m.due_text,
                rule=m.rule,
                origin=origin,
                created_at=sent_at,
            )
        )
    return ids


def record_checks(conn: sqlite3.Connection, draft: str, *, customer_id: str, thread_id: str) -> list[int]:
    """Every [[CHECK: ...]] placeholder in a draft is also a row, so it is not lost
    if the agent deletes it from the draft without doing it."""
    ids = []
    for what in _CHECK.findall(draft or ""):
        what = what.strip()
        dup = conn.execute(
            "SELECT id FROM actions WHERE kind='check' AND thread_id=? AND what=?", (thread_id, what)
        ).fetchone()
        if not dup:
            ids.append(add(conn, "check", what, customer_id=customer_id, thread_id=thread_id, origin="draft"))
    return ids


def mark_done(conn: sqlite3.Connection, action_id: int) -> None:
    conn.execute("UPDATE actions SET status='done', done_at=? WHERE id=?", (_now().isoformat(), action_id))


def reopen(conn: sqlite3.Connection, action_id: int) -> None:
    conn.execute("UPDATE actions SET status='open', done_at=NULL WHERE id=?", (action_id,))


def open_rows(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM actions WHERE status='open' ORDER BY due_at IS NULL, due_at, created_at"
    ).fetchall()


def leaked_placeholders(conn: sqlite3.Connection, since: datetime | None = None) -> list[sqlite3.Row]:
    """Sent replies that still contained [[...]]: each one is a miss."""
    rows = conn.execute(
        "SELECT id, thread_id, customer_id, sent_at, sent_text FROM drafts WHERE sent_text LIKE '%[[%'"
    ).fetchall()
    out = [r for r in rows if PLACEHOLDER_RE.search(r["sent_text"] or "")]
    if since:
        out = [r for r in out if r["sent_at"] and r["sent_at"] >= since.isoformat()]
    return out


def digest(conn: sqlite3.Connection, now: datetime | None = None, firm_name: str = "") -> str:
    """Plain-text morning digest. In deployment it becomes a Gmail draft that a
    person sends; the tool never sends it."""
    now = now or _now()
    end_today = now.replace(hour=23, minute=59, second=59)
    rows = open_rows(conn)
    overdue = [r for r in rows if r["kind"] == "commitment" and r["due_at"] and r["due_at"] < now.isoformat()]
    today = [
        r for r in rows
        if r["kind"] == "commitment" and r["due_at"] and now.isoformat() <= r["due_at"] <= end_today.isoformat()
    ]
    undated = [r for r in rows if r["kind"] == "commitment" and not r["due_at"]]
    checks = [r for r in rows if r["kind"] == "check"]
    spikes = [r for r in rows if r["kind"] == "spike"]
    leaks = leaked_placeholders(conn, since=now - timedelta(days=1))

    def fmt(r) -> str:
        due = f" (due {r['due_at'][:16].replace('T', ' ')})" if r["due_at"] else f" ({r['due_text']})" if r["due_text"] else ""
        who = f" [customer {r['customer_id']}, thread {r['thread_id']}]" if r["customer_id"] else ""
        return f"  - #{r['id']} {r['what']}{due}{who}"

    parts = [f"Support digest{' for ' + firm_name if firm_name else ''}, {now:%A %d %B %Y}", ""]
    for title, items in [
        ("OVERDUE promises", overdue),
        ("Due today", today),
        ("Promises with no clear deadline", undated),
        ("Checks waiting to be made", checks),
        ("Spikes still open", spikes),
    ]:
        if items:
            parts.append(f"{title} ({len(items)}):")
            parts += [fmt(r) for r in items]
            parts.append("")
    if leaks:
        parts.append(f"Placeholders that reached a customer in the last day ({len(leaks)}):")
        parts += [f"  - thread {r['thread_id']}, sent {r['sent_at'][:16]}" for r in leaks]
        parts.append("")
    if len(parts) == 2:
        parts.append("Nothing open. Nothing overdue.")
    return "\n".join(parts).rstrip() + "\n"


def customer_history(conn: sqlite3.Connection, customer_id: str, before: str | None = None, limit: int = 10):
    """This customer's earlier messages and the replies they got, newest first."""
    sql = "SELECT asked_at, question, answer, thread_id FROM pairs WHERE customer_id = ?"
    args: list = [customer_id]
    if before:
        sql += " AND asked_at < ?"
        args.append(before)
    sql += " ORDER BY asked_at DESC LIMIT ?"
    args.append(limit)
    return conn.execute(sql, args).fetchall()
