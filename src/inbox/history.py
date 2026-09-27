"""Onboarding an existing support mailbox: read what the team already wrote.

A team switching this on has months of correspondence. That history becomes:
  - the knowledge base: every reply the team sent, paired with what it answered,
    with its real date (so newer answers can beat older ones);
  - customer context: each customer's earlier conversations, by address;
  - tone: drafts are built from, and styled like, the team's own replies.

  python -m inbox --firm demo gmail-import --months 12

Safe to re-run: a message already imported is skipped.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from .config import Firm
from .mailtext import strip_frame, strip_quoted, strip_signature
from .textclean import clean, is_deflection, redact


@dataclass
class ImportReport:
    threads: int = 0
    pairs: int = 0
    messages: int = 0
    skipped_automated: int = 0


def _body(mail) -> str:
    return strip_signature(strip_quoted(mail.raw_body)).strip()


def import_mailbox(mb, conn: sqlite3.Connection, firm: Firm, months: int = 12, log=print, pause: float = 0.0) -> ImportReport:
    """Every thread the team replied in over the last `months`, oldest reply first."""
    rep = ImportReport()
    thread_ids = mb.sent_thread_ids(days=int(months * 31))
    log(f"[history] {len(thread_ids)} conversations with a reply in the last {months} months")
    for n, tid in enumerate(thread_ids, 1):
        msgs = [m for m in mb.thread(tid) if "DRAFT" not in m["labels"]]
        rep.threads += 1
        waiting: list[dict] = []          # customer messages not yet answered
        answered_before = False
        with conn:
            for m in msgs:
                mail = m["mail"]
                at = datetime.fromtimestamp(m["at"] / 1000, tz=timezone.utc).isoformat()
                ours = mail.from_addr == mb.me
                if not ours and mail.automated:
                    rep.skipped_automated += 1
                    continue
                body = _body(mail)
                cur = conn.execute(
                    "INSERT OR IGNORE INTO messages VALUES (?,?,?,?,?,?,?)",
                    (m["id"], tid, None, mb.me if ours else mail.from_addr, 0 if ours else 1, at,
                     f"{mail.subject}\n\n{body}" if not ours else body),
                )
                rep.messages += cur.rowcount
                if not ours:
                    waiting.append({"id": m["id"], "at": at, "from": mail.from_addr, "body": body})
                    continue
                if not waiting:
                    continue      # the team wrote first (an announcement): nothing to pair
                question = clean("\n\n".join(w["body"] for w in waiting))
                answer = redact(strip_frame(clean(body, keep_urls=True)), firm.public_numbers)
                if question and answer:
                    cur = conn.execute(
                        """INSERT OR IGNORE INTO pairs (thread_id, customer_msg_id, customer_id, asked_at, answered_at,
                           question, answer, answer_raw, is_followup, is_deflection, source)
                           VALUES (?,?,?,?,?,?,?,?,?,?, 'mailbox')""",
                        (tid, waiting[-1]["id"], waiting[-1]["from"], waiting[-1]["at"], at, question, answer, body,
                         int(answered_before), int(is_deflection(answer))),
                    )
                    rep.pairs += cur.rowcount
                waiting, answered_before = [], True
        if n % 25 == 0:
            log(f"[history] {n}/{len(thread_ids)} conversations, {rep.pairs} approved answers so far")
        if pause:
            time.sleep(pause)   # stay well inside Gmail's per-second quota on big mailboxes
    log(f"[history] done: {rep}")
    return rep
