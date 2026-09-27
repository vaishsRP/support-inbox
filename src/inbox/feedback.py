"""Step 8: capture what the human actually sent.

Three things happen to every sent reply:
1. It is diffed against the draft (plain text, review line and agent notes
   removed): the edit measure the spec cares about.
2. The outgoing rules run on it, so a promise the agent typed in by hand still
   becomes an action-list row, and any [[...]] left in it is a miss.
3. It joins the pool of approved answers. This is the answer-once loop.
"""

from __future__ import annotations

import re
import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo

from rapidfuzz.distance import Indel

from . import actions
from .rules import Rule, check_outgoing
from .textclean import clean, redact

_NOTE_LINE = re.compile(r"^\s*\[\[(REVIEW|AGENT NOTE):[^\]]*\]\]\s*$", re.M)
DISCARD_BELOW = 0.25   # less similar than this: the agent threw the draft away and wrote their own


def draft_body(draft: str | None) -> str:
    """The part of a draft meant for the customer: no review line, no agent notes."""
    return re.sub(r"\s+", " ", _NOTE_LINE.sub("", draft or "")).strip()


def normalise(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def edit_similarity(draft: str | None, sent: str) -> float:
    """1 - normalised edit distance on plain text. 1.0 = sent untouched."""
    return float(Indel.normalized_similarity(normalise(draft_body(draft)), normalise(sent)))


def outcome_for(draft: str | None, similarity: float | None) -> str:
    if not draft:
        return "no_draft"
    if similarity is None:
        return "pending"
    if similarity >= 0.999:
        return "sent_as_is"
    if similarity < DISCARD_BELOW:
        return "discarded"
    return "edited"


def record_sent(
    conn: sqlite3.Connection,
    draft_id: int,
    sent_text: str,
    sent_at: datetime,
    rules: list[Rule],
    public_numbers: list[str] = (),
    add_to_pool: bool = True,
    owner: str | None = None,
    tz: str | None = None,
) -> dict:
    """`tz` is the firm's time zone: "someone will call you today" means today where
    the firm is, not in UTC."""
    row = conn.execute("SELECT * FROM drafts WHERE id = ?", (draft_id,)).fetchone()
    if row is None:
        raise KeyError(f"no draft {draft_id}")
    answered = row["route"] in ("reuse", "docs")   # skeletons and holding replies are not drafts to edit
    sim = edit_similarity(row["draft_text"], sent_text) if row["draft_text"] and answered else None
    outcome = outcome_for(row["draft_text"] if answered else None, sim)
    local = sent_at.astimezone(ZoneInfo(tz)) if tz else sent_at
    hits = check_outgoing(sent_text, rules, sent_at=local)
    with conn:
        conn.execute(
            "UPDATE drafts SET sent_text=?, sent_at=?, edit_similarity=?, outcome=? WHERE id=?",
            (sent_text, sent_at.isoformat(), sim, outcome, draft_id),
        )
        new_actions = actions.record_commitments(
            conn, hits, customer_id=row["customer_id"], thread_id=row["thread_id"], origin="sent", sent_at=sent_at
        )
        pair_id = None
        if add_to_pool:
            pair_id = _add_pair(conn, row, sent_text, sent_at, public_numbers, owner)
    return {"outcome": outcome, "edit_similarity": sim, "actions": new_actions, "pair_id": pair_id}


def _add_pair(conn, draft_row, sent_text: str, sent_at: datetime, public_numbers, owner=None) -> int | None:
    q = conn.execute(
        "SELECT text, created_at, parent_id FROM messages WHERE id = ?", (draft_row["customer_msg_id"],)
    ).fetchone()
    question = clean(q["text"]) if q else None
    if not question:
        return None
    # The greeting and sign-off are the customer's name and the firm's frame: stored
    # answers keep the body, so the next draft gets its own greeting and name.
    from .mailtext import strip_frame

    answer = redact(strip_frame(clean(sent_text, keep_urls=True)), list(public_numbers))
    if not answer:
        return None
    cur = conn.execute(
        """INSERT INTO pairs (thread_id, customer_msg_id, customer_id, asked_at, answered_at, question, answer,
           answer_raw, is_followup, is_deflection, source, owner)
           VALUES (?,?,?,?,?,?,?,?,?,0,'sent',?)
           ON CONFLICT(customer_msg_id) DO UPDATE SET answer=excluded.answer, answer_raw=excluded.answer_raw,
           answered_at=excluded.answered_at, source='sent', owner=excluded.owner""",
        (
            draft_row["thread_id"],
            draft_row["customer_msg_id"],
            draft_row["customer_id"],
            q["created_at"],
            sent_at.isoformat(),
            question,
            answer,
            sent_text,
            int(q["parent_id"] is not None),
            owner,
        ),
    )
    return conn.execute("SELECT id FROM pairs WHERE customer_msg_id = ?", (draft_row["customer_msg_id"],)).fetchone()[0]


def measures(conn: sqlite3.Connection, run: str | None = None) -> dict:
    """The spec's three numbers, as far as the stored data allows. The third one
    (commitments caught) needs a hand check on a sample; this gives the inputs."""
    total = conn.execute("SELECT COUNT(*) FROM drafts WHERE run IS ?", (run,)).fetchone()[0]
    by_route = dict(conn.execute("SELECT route, COUNT(*) FROM drafts WHERE run IS ? GROUP BY route", (run,)).fetchall())
    drafted = by_route.get("reuse", 0) + by_route.get("docs", 0)
    sims = [r[0] for r in conn.execute("SELECT edit_similarity FROM drafts WHERE edit_similarity IS NOT NULL AND run IS ?", (run,))]
    outcomes = dict(conn.execute("SELECT outcome, COUNT(*) FROM drafts WHERE run IS ? GROUP BY outcome", (run,)).fetchall())
    tracked = conn.execute("SELECT COUNT(*) FROM actions WHERE kind='commitment'").fetchone()[0]
    leaks = len(actions.leaked_placeholders(conn))
    sims.sort()

    def q(p):
        return sims[min(len(sims) - 1, int(p * len(sims)))] if sims else None

    return {
        "mails": total,
        "drafted": drafted,
        "refused": by_route.get("refused", 0),
        "blocked": by_route.get("blocked", 0),
        "failed": by_route.get("failed", 0),
        "draft_rate": drafted / total if total else None,
        "edit_similarity_median": q(0.5),
        "edit_similarity_p25": q(0.25),
        "edit_similarity_p75": q(0.75),
        "outcomes": outcomes,
        "commitments_tracked": tracked,
        "placeholders_leaked": leaks,
    }
