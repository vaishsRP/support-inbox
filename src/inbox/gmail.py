"""Gmail: the only module that talks to a mailbox.

It can do exactly four kinds of thing: read messages, create or update a draft, apply
labels, and read what was sent. There is no code here that sends mail, and a test fails
the build if any appears anywhere. Gmail's own permissions cannot enforce that (the
scope that allows drafts also allows sending), so this file is where it is enforced.

  python -m inbox --firm demo gmail-auth     # once: sign in in the browser, token saved
  python -m inbox --firm demo gmail-run      # poll every minute or two
  python -m inbox --firm demo gmail-once     # one poll, for testing
"""

from __future__ import annotations

import base64
import hashlib
import re
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path

from . import actions, feedback
from .config import ROOT, Firm, env
from .draft import Drafter, Incoming
from .mailtext import Mail, parse, strip_quoted, strip_signature
from .rules import Rule

# Read, label, and manage drafts. Nothing in this code uses it to send.
SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]
# Parents first: Gmail nests "AI/draft-ready/high" under "AI/draft-ready" only if it exists.
LABELS = [
    "AI", "AI/draft-ready", "AI/draft-ready/high", "AI/draft-ready/medium", "AI/draft-ready/low",
    "AI/needs-approval", "AI/no-answer", "AI/skipped", "AI/context",
]
# Labels earlier versions created; removed so every email has exactly one AI label.
OBSOLETE_LABELS = [
    "AI/confidence-high", "AI/confidence-medium", "AI/confidence-low", "AI/needs-authority", "AI/digest",
    "AI/automated", "AI/context-added",
]
NOTE_SUBJECT = re.compile(r"^\s*(note|policy)\s*:\s*(?P<title>.+?)\s*$", re.I)
NOTE_DAYS = re.compile(r"\bfor (\d{1,3}) days?\b", re.I)

STATE_SCHEMA = """
CREATE TABLE IF NOT EXISTS gmail_state (key TEXT PRIMARY KEY, value TEXT);
-- One row per customer message the tool handled in Gmail.
CREATE TABLE IF NOT EXISTS gmail_threads (
    message_id   TEXT PRIMARY KEY,          -- Gmail id of the customer's message
    thread_id    TEXT NOT NULL,
    draft_row    INTEGER,                   -- drafts.id in our database; null when skipped
    gmail_draft  TEXT,                      -- Gmail draft id, null when nothing was drafted
    draft_hash   TEXT,                      -- hash of what we wrote, to spot human edits
    handled_at   TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'open'   -- open | sent | closed
);
"""


def _gone(e: Exception) -> bool:
    """Gmail's 'not found': the message was deleted, or it was a draft that got sent."""
    return "404" in str(e) or "notFound" in str(e) or isinstance(e, KeyError)


def _hash(text: str) -> str:
    return hashlib.sha1(" ".join((text or "").split()).encode()).hexdigest()


# ---- The mailbox wrapper -------------------------------------------------------------


class Mailbox:
    """Wraps the Gmail API service. The service object never leaves this class."""

    def __init__(self, service, me: str):
        self._svc = service
        self.me = me.lower()
        self._label_ids: dict[str, str] = {}

    @classmethod
    def connect(cls, firm: Firm, interactive: bool = False) -> "Mailbox":
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build

        token = firm.data_dir / "gmail_token.json"
        creds = Credentials.from_authorized_user_file(str(token), SCOPES) if token.exists() else None
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception:
                creds = None   # testing-mode tokens expire after about a week
        if not creds or not creds.valid:
            if not interactive:
                raise SystemExit("Gmail login missing or expired. Run: python -m inbox --firm "
                                 f"{firm.key} gmail-auth")
            from google_auth_oauthlib.flow import InstalledAppFlow

            secrets = ROOT / (env("GMAIL_CREDENTIALS_PATH") or "data/credentials.json")
            flow = InstalledAppFlow.from_client_secrets_file(str(secrets), SCOPES)
            creds = flow.run_local_server(port=0, prompt="consent")
            token.write_text(creds.to_json(), encoding="utf-8")
        svc = build("gmail", "v1", credentials=creds, cache_discovery=False)
        me = svc.users().getProfile(userId="me").execute()["emailAddress"]
        return cls(svc, me)

    # -- read --
    def history_id(self) -> str:
        return str(self._svc.users().getProfile(userId="me").execute()["historyId"])

    def new_inbox_ids(self, since_history: str | None) -> tuple[list[str], str]:
        """Messages added to the inbox since `since_history`. The first run starts from
        now: an existing inbox is not drafted retroactively."""
        users = self._svc.users()
        if not since_history:
            return [], self.history_id()
        if since_history:
            try:
                ids, page, latest = [], None, since_history
                while True:
                    # New mail, and mail moved into the inbox (for example "Not spam").
                    r = users.history().list(userId="me", startHistoryId=since_history,
                                             historyTypes=["messageAdded", "labelAdded"], labelId="INBOX",
                                             pageToken=page).execute()
                    for h in r.get("history", []):
                        ids += [a["message"]["id"] for a in h.get("messagesAdded", [])]
                        ids += [a["message"]["id"] for a in h.get("labelsAdded", []) if "INBOX" in a.get("labelIds", [])]
                    latest = str(r.get("historyId", latest))
                    page = r.get("nextPageToken")
                    if not page:
                        return list(dict.fromkeys(ids)), latest
            except Exception as e:   # history ids expire after about a week
                if "404" not in str(e) and "notFound" not in str(e):
                    raise
        r = users.messages().list(userId="me", q="in:inbox newer_than:1d", maxResults=100).execute()
        return [m["id"] for m in r.get("messages", [])], self.history_id()

    def sent_thread_ids(self, days: int) -> list[str]:
        """Conversations the team replied in, oldest first (for importing history)."""
        ids, page = [], None
        while True:
            r = self._svc.users().messages().list(userId="me", q=f"in:sent newer_than:{int(days)}d",
                                                  maxResults=500, pageToken=page).execute()
            ids += [m["threadId"] for m in r.get("messages", [])]
            page = r.get("nextPageToken")
            if not page:
                return list(dict.fromkeys(reversed(ids)))   # the API lists newest first

    def recent_inbox_ids(self, hours: int) -> list[str]:
        r = self._svc.users().messages().list(userId="me", q=f"in:inbox newer_than:{int(hours)}h", maxResults=100).execute()
        return [m["id"] for m in r.get("messages", [])]

    def get(self, msg_id: str) -> tuple[Mail, dict]:
        r = self._svc.users().messages().get(userId="me", id=msg_id, format="raw").execute()
        return parse(base64.urlsafe_b64decode(r["raw"])), r

    def thread(self, thread_id: str) -> list[dict]:
        # threads.get has no raw format: list the thread's messages, then fetch each one raw.
        r = self._svc.users().threads().get(userId="me", id=thread_id, format="minimal").execute()
        out = []
        for m in r.get("messages", []):
            try:
                full = self._svc.users().messages().get(userId="me", id=m["id"], format="raw").execute()
            except Exception as e:
                if _gone(e):
                    continue      # a draft that was sent or deleted a moment ago
                raise
            mail = parse(base64.urlsafe_b64decode(full["raw"]))
            out.append({"id": m["id"], "labels": m.get("labelIds", []), "at": int(m["internalDate"]), "mail": mail})
        return sorted(out, key=lambda x: x["at"])

    # -- labels --
    def ensure_labels(self) -> None:
        existing = self._svc.users().labels().list(userId="me").execute().get("labels", [])
        self._label_ids = {l["name"]: l["id"] for l in existing}
        for name in OBSOLETE_LABELS:
            if name in self._label_ids:
                self._svc.users().labels().delete(userId="me", id=self._label_ids.pop(name)).execute()
        for name in LABELS:
            if name not in self._label_ids:
                made = self._svc.users().labels().create(
                    userId="me", body={"name": name, "labelListVisibility": "labelShow", "messageListVisibility": "show"}
                ).execute()
                self._label_ids[name] = made["id"]

    def label(self, msg_id: str, names: list[str]) -> None:
        ids = [self._label_ids[n] for n in names if n in self._label_ids]
        if ids:
            self._svc.users().messages().modify(userId="me", id=msg_id, body={"addLabelIds": ids}).execute()

    # -- drafts --
    def put_draft(self, *, to: str, subject: str, body: str, thread_id: str | None = None,
                  in_reply_to: str | None = None, references: str = "", draft_id: str | None = None) -> str:
        """Create, or replace, a draft reply in the thread. Returns the draft id."""
        msg = EmailMessage()
        msg["To"] = to
        msg["From"] = self.me
        msg["Subject"] = subject if subject.lower().startswith("re:") or not in_reply_to else f"Re: {subject}"
        if in_reply_to:
            msg["In-Reply-To"] = in_reply_to
            msg["References"] = (references + " " + in_reply_to).strip()
        msg.set_content(body)
        payload = {"message": {"raw": base64.urlsafe_b64encode(msg.as_bytes()).decode()}}
        if thread_id:
            payload["message"]["threadId"] = thread_id
        drafts = self._svc.users().drafts()
        if draft_id:
            return drafts.update(userId="me", id=draft_id, body=payload).execute()["id"]
        return drafts.create(userId="me", body=payload).execute()["id"]

    def draft_text(self, draft_id: str) -> str | None:
        """The draft's current body, or None if it no longer exists (sent or deleted)."""
        try:
            r = self._svc.users().drafts().get(userId="me", id=draft_id, format="raw").execute()
        except Exception as e:
            if "404" in str(e) or "notFound" in str(e):
                return None
            raise
        return parse(base64.urlsafe_b64decode(r["message"]["raw"])).raw_body


# ---- One polling pass ------------------------------------------------------------------


@dataclass
class PassReport:
    seen: int = 0
    drafted: int = 0
    refused: int = 0
    blocked: int = 0
    skipped: int = 0
    sent_captured: int = 0


def _state(conn: sqlite3.Connection, key: str, value: str | None = None) -> str | None:
    if value is None:
        r = conn.execute("SELECT value FROM gmail_state WHERE key = ?", (key,)).fetchone()
        return r[0] if r else None
    with conn:
        conn.execute("INSERT INTO gmail_state VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))
    return value


def poll_once(mb: Mailbox, drafter: Drafter, conn: sqlite3.Connection, firm: Firm, rules: list[Rule],
              log=print, catch_up_hours: int | None = None) -> PassReport:
    """One pass. `catch_up_hours` also handles inbox mail from the last N hours that
    arrived before the watcher's starting point (used once, by hand)."""
    conn.executescript(STATE_SCHEMA)
    rep = PassReport()
    ids, latest = mb.new_inbox_ids(_state(conn, "history_id"))
    if catch_up_hours:
        ids = list(dict.fromkeys(mb.recent_inbox_ids(catch_up_hours) + ids))
    for msg_id in ids:
        if conn.execute("SELECT 1 FROM gmail_threads WHERE message_id = ?", (msg_id,)).fetchone():
            continue   # already handled: restarts never process a mail twice
        try:
            mail, meta = mb.get(msg_id)
        except Exception as e:
            if _gone(e):
                continue          # sent drafts and deleted mail vanish; nothing to handle
            raise
        rep.seen += 1
        thread_id = meta["threadId"]
        received = datetime.fromtimestamp(int(meta["internalDate"]) / 1000, tz=timezone.utc)
        def skip(why: str) -> None:
            rep.skipped += 1
            with conn:
                conn.execute(
                    "INSERT OR IGNORE INTO gmail_threads (message_id, thread_id, draft_row, handled_at, status) "
                    "VALUES (?,?,NULL,?, 'closed')", (msg_id, thread_id, datetime.now(timezone.utc).isoformat()))
            log(f"[gmail] skipped {mail.subject!r}: {why}")

        note = NOTE_SUBJECT.match(mail.subject or "")
        if mail.from_addr == mb.me and note:
            add_context_from_mail(conn, drafter, mail, note, received)
            mb.label(msg_id, ["AI/context"])
            skip(f"context added: {note.group('title')}")
            continue
        if mail.from_addr == mb.me:
            skip("our own message")
            continue
        if mail.automated:
            mb.label(msg_id, ["AI/skipped"])
            skip(mail.automated)
            continue
        thread = mb.thread(thread_id)
        later = [t for t in thread if t["at"] > int(meta["internalDate"])]
        if any(t["mail"].from_addr == mb.me and "DRAFT" not in t["labels"] for t in later):
            skip("a person already answered after it")
            continue
        earlier = [t for t in thread if t["at"] < int(meta["internalDate"]) and "DRAFT" not in t["labels"]]
        context = [("firm" if t["mail"].from_addr == mb.me else "customer", t["mail"].body) for t in earlier]
        with conn:
            conn.execute(
                "INSERT OR IGNORE INTO messages VALUES (?,?,?,?,1,?,?)",
                (msg_id, thread_id, None, mail.from_addr, received.isoformat(), f"{mail.subject}\n\n{mail.body}"),
            )
        res = drafter.handle(Incoming(
            msg_id, thread_id, mail.from_addr, mail.body, received, context,
            sender_name=mail.from_name, sender_addr=mail.from_addr, attachments=mail.attachments,
        ))
        draft_row = conn.execute("SELECT MAX(id) FROM drafts").fetchone()[0]
        gmail_draft = None
        if res.draft:
            # A second mail in a thread nobody has answered yet: replace our own draft if it
            # is untouched, and never overwrite one a person has started editing.
            prev = conn.execute(
                "SELECT * FROM gmail_threads WHERE thread_id = ? AND status = 'open' AND gmail_draft IS NOT NULL "
                "ORDER BY handled_at DESC LIMIT 1", (thread_id,)).fetchone()
            reuse_id, edited = None, False
            if prev:
                current = mb.draft_text(prev["gmail_draft"])
                if current is not None and _hash(current) == prev["draft_hash"]:
                    reuse_id = prev["gmail_draft"]
                    with conn:
                        conn.execute("UPDATE gmail_threads SET status = 'closed' WHERE message_id = ?", (prev["message_id"],))
                elif current is not None:
                    edited = True
                    log(f"[gmail] left the edited draft in thread {thread_id} alone")
            if not edited:
                gmail_draft = mb.put_draft(to=mail.from_addr, subject=mail.subject, body=res.draft, thread_id=thread_id,
                                           in_reply_to=mail.message_id, references=mail.references, draft_id=reuse_id)
        if res.route in ("reuse", "docs"):
            rep.drafted += 1
        elif res.route == "blocked":
            rep.blocked += 1
        else:
            rep.refused += 1
        mb.label(msg_id, res.labels)
        with conn:
            conn.execute(
                "INSERT INTO gmail_threads (message_id, thread_id, draft_row, gmail_draft, draft_hash, handled_at) "
                "VALUES (?,?,?,?,?,?)",
                (msg_id, thread_id, draft_row, gmail_draft, _hash(res.draft) if res.draft else None,
                 datetime.now(timezone.utc).isoformat()),
            )
        log(f"[gmail] {res.route:8s} {mail.subject!r} from {mail.from_addr}")
    _state(conn, "history_id", latest)
    rep.sent_captured = capture_sent(mb, conn, firm, rules, log)
    sync_action_list(mb, conn, log)
    sync_context(mb, conn, drafter, log)
    return rep


def capture_sent(mb: Mailbox, conn: sqlite3.Connection, firm: Firm, rules: list[Rule], log=print) -> int:
    """What the person actually sent: diffed, tracked, and added to the answer pool."""
    n = 0
    for row in conn.execute("SELECT * FROM gmail_threads WHERE status = 'open'").fetchall():
        handled = datetime.fromisoformat(row["handled_at"])
        sent = [t for t in mb.thread(row["thread_id"])
                if "SENT" in t["labels"] and "DRAFT" not in t["labels"]
                and t["at"] / 1000 > handled.timestamp() - 5]
        if not sent:
            if row["gmail_draft"] and (datetime.now(timezone.utc) - handled).days >= 14:
                with conn:
                    conn.execute("UPDATE gmail_threads SET status = 'closed' WHERE message_id = ?", (row["message_id"],))
            continue
        first = sent[0]
        text = strip_signature(strip_quoted(first["mail"].raw_body))
        at = datetime.fromtimestamp(first["at"] / 1000, tz=timezone.utc)
        feedback.record_sent(conn, row["draft_row"], text, at, rules, firm.public_numbers, tz=firm.timezone)
        with conn:
            conn.execute("UPDATE gmail_threads SET status = 'sent' WHERE message_id = ?", (row["message_id"],))
        n += 1
        log(f"[gmail] captured sent reply in thread {row['thread_id']}")
    return n


def add_context_from_mail(conn, drafter: Drafter, mail: Mail, note: re.Match, received: datetime) -> int:
    """'Note: Heating outage Block C' sent to the support inbox from itself becomes a
    dated note (14 days, or 'for N days' in the subject). 'Policy: Deposits and refunds'
    adds that policy page, replacing an existing page with the same title. Only the
    support address itself can do this, so a customer cannot plant context."""
    from .docs import add_doc

    title = NOTE_DAYS.sub("", note.group("title")).strip(" -,")
    days = NOTE_DAYS.search(note.group("title"))
    kind = "standing" if note.group(1).lower() == "policy" else "note"
    with conn:
        if kind == "standing":
            conn.execute("UPDATE docs SET retired = 1 WHERE kind = 'standing' AND lower(title) = lower(?)", (title,))
        doc_id = add_doc(conn, kind, title, mail.body or title, created_at=received,
                         expires_in_days=int(days.group(1)) if days else None)
    drafter.refresh_docs()
    return doc_id


# ---- Drafts that are pages: the action list and the context ---------------------------
#
# Two drafts in the support inbox work as editable pages. The assistant writes them; a
# person edits them in Gmail like any draft: delete a line to close or remove it, type
# under the last line to add something. Nobody sends them.
#
# Edits are read only once the draft has stayed the same for a whole check, so text a
# person is still typing is never picked up half-finished.

ACTION_SUBJECT = "Action list (kept up to date by the support assistant)"
CONTEXT_SUBJECT = "Context for the support assistant (notes and policies)"
_ROW_ID = re.compile(r"#(\d+)\b")
_DOC_ID = re.compile(r"\[(N|P)(\d+)\]")
ADD_TODO = "ADD A TO-DO BELOW THIS LINE (one per line)"
ADD_NOTE = "ADD A NOTE BELOW THIS LINE (first line is the title, add \"for 3 days\" to it to set how long; then the details)"


def _below(text: str, marker: str) -> str:
    i = text.find(marker.split(" (")[0])
    if i < 0:
        return ""
    rest = text[i:].split("\n", 1)
    return rest[1].strip() if len(rest) > 1 else ""


def _read_page(mb: Mailbox, conn: sqlite3.Connection, key: str) -> tuple[str | None, str | None, bool]:
    """(draft id, current text, ready): ready is False while a person may still be typing."""
    draft_id = _state(conn, f"{key}_draft")
    if not draft_id:
        return None, None, False
    current = mb.draft_text(draft_id)
    if current is None:
        return None, None, False          # deleted or sent by mistake: a fresh one is made
    if _hash(current) == _state(conn, f"{key}_written"):
        return draft_id, current, False   # untouched since we wrote it
    stable = _state(conn, f"{key}_seen") == _hash(current)
    _state(conn, f"{key}_seen", _hash(current))
    return draft_id, current, stable


def _write_page(mb: Mailbox, conn: sqlite3.Connection, key: str, subject: str, text: str, draft_id: str | None) -> None:
    draft_id = mb.put_draft(to=mb.me, subject=subject, body=text, draft_id=draft_id)
    _state(conn, f"{key}_draft", draft_id)
    _state(conn, f"{key}_written", _hash(mb.draft_text(draft_id) or text))


def action_list_text(conn: sqlite3.Connection, now: datetime | None = None) -> tuple[str, list[int]]:
    now = now or datetime.now(timezone.utc)
    rows = actions.open_rows(conn)

    def who(r) -> str:
        m = conn.execute("SELECT author, text FROM messages WHERE thread_id = ? AND inbound = 1 ORDER BY created_at LIMIT 1",
                         (r["thread_id"],)).fetchone() if r["thread_id"] else None
        if not m:
            return ""
        subject = (m["text"] or "").split("\n", 1)[0][:60]
        return f"  ({m['author']}, \"{subject}\")"

    def line(r) -> str:
        if r["due_at"]:
            due = datetime.fromisoformat(r["due_at"])
            when = ("OVERDUE, was due " if due < now else "due ") + due.strftime("%a %d %b %H:%M")
        else:
            when = r["due_text"] or ""
        return f"#{r['id']}  {r['what']}" + (f"  [{when}]" if when else "") + who(r)

    parts = [
        f"Updated {now:%a %d %b %H:%M} UTC. Delete a line when it is done: it closes on the next check.",
        "",
    ]
    for title, kind in (("PROMISES MADE TO CUSTOMERS", "commitment"), ("THINGS TO DO OR LOOK UP", "check"), ("SPIKES", "spike")):
        items = [r for r in rows if r["kind"] == kind]
        if items:
            parts += [title] + [line(r) for r in items] + [""]
    if len(parts) == 2:
        parts += ["Nothing open.", ""]
    parts += [ADD_TODO, ""]
    return "\n".join(parts), [r["id"] for r in rows]


def sync_action_list(mb: Mailbox, conn: sqlite3.Connection, log=print) -> int:
    """The team's to-do list as a draft. Deleted lines are marked done; lines typed under
    the last heading become to-dos. Returns how many rows were closed."""
    draft_id, current, ready = _read_page(mb, conn, "action")
    written = [int(x) for x in (_state(conn, "action_ids") or "").split(",") if x]
    closed = added = 0
    if current is not None and not ready and _hash(current) != _state(conn, "action_written"):
        return 0                          # a person is editing: wait until it settles
    if ready:
        still_there = {int(x) for x in _ROW_ID.findall(current)}
        with conn:
            for rid in written:
                if rid not in still_there:
                    actions.mark_done(conn, rid)
                    closed += 1
            for line in _below(current, ADD_TODO).splitlines():
                if line.strip():
                    actions.add(conn, "check", line.strip(), origin="manual")
                    added += 1
        if closed or added:
            log(f"[gmail] action list: {closed} ticked off, {added} added")
    text, ids = action_list_text(conn)
    if draft_id and ids == written and not ready:
        return closed                     # nothing changed: leave the draft alone
    _write_page(mb, conn, "action", ACTION_SUBJECT, text, draft_id)
    _state(conn, "action_ids", ",".join(map(str, ids)))
    return closed


def context_text(conn: sqlite3.Connection, now: datetime | None = None) -> tuple[str, list[str]]:
    from .docs import live_docs

    now = now or datetime.now(timezone.utc)
    docs = [d for d in live_docs(conn, now) if d["owner"] is None]
    notes = [d for d in docs if d["kind"] == "note"]
    pages = [d for d in docs if d["kind"] == "standing"]
    parts = [
        "What the assistant knows besides past emails. Edit this draft to change it; nobody sends it.",
        "Delete a note (its whole block) to remove it, for example a false alarm. Delete a policy line to stop using that page.",
        "To replace a policy page, email this address from itself with the subject \"Policy: <same title>\" and the new text.",
        "",
        "CURRENT NOTES",
    ]
    if notes:
        for d in notes:
            until = datetime.fromisoformat(d["expires_at"]).strftime("%a %d %b")
            parts += [f"[N{d['id']}] {d['title']} (until {until})", d["body"], ""]
    else:
        parts += ["None.", ""]
    parts.append("POLICY PAGES")
    parts += [f"[P{d['id']}] {d['title']}" for d in pages] or ["None."]
    parts += ["", ADD_NOTE, ""]
    ids = [f"N{d['id']}" for d in notes] + [f"P{d['id']}" for d in pages]
    return "\n".join(parts), ids


def sync_context(mb: Mailbox, conn: sqlite3.Connection, drafter: Drafter, log=print) -> None:
    """The context page as a draft: remove notes or policies by deleting them, add a note
    by typing it at the bottom."""
    from .docs import add_doc, retire_doc

    draft_id, current, ready = _read_page(mb, conn, "context")
    written = [x for x in (_state(conn, "context_ids") or "").split(",") if x]
    if current is not None and not ready and _hash(current) != _state(conn, "context_written"):
        return                            # a person is editing: wait until it settles
    changed = False
    if ready:
        present = {f"{k}{n}" for k, n in _DOC_ID.findall(current)}
        with conn:
            for key in written:
                if key not in present:
                    retire_doc(conn, int(key[1:]))
                    changed = True
            new = _below(current, ADD_NOTE)
            if new:
                first, _, rest = new.partition("\n")
                days = NOTE_DAYS.search(first)
                title = NOTE_DAYS.sub("", first).strip(" -,:") or "Note"
                add_doc(conn, "note", title, rest.strip() or title,
                        expires_in_days=int(days.group(1)) if days else None)
                changed = True
        if changed:
            drafter.refresh_docs()
            log("[gmail] context updated from the context draft")
    text, ids = context_text(conn)
    if draft_id and ids == written and not ready:
        return
    _write_page(mb, conn, "context", CONTEXT_SUBJECT, text, draft_id)
    _state(conn, "context_ids", ",".join(ids))


def digest_draft(mb: Mailbox, conn: sqlite3.Connection, firm: Firm) -> str | None:
    """The morning digest, as a draft to the manager. A person sends it."""
    to = (firm.raw.get("gmail") or {}).get("digest_to")
    if not to:
        return None
    text = actions.digest(conn, firm_name=firm.name)
    return mb.put_draft(to=to, subject=f"Support digest {datetime.now():%d %b}", body=text)


def run_forever(firm: Firm, drafter: Drafter, rules: list[Rule], every: int = 90, log=print) -> None:
    mb = Mailbox.connect(firm)
    mb.ensure_labels()
    conn = drafter.conn
    conn.executescript(STATE_SCHEMA)
    log(f"[gmail] watching {mb.me} every {every}s. Ctrl+C to stop.")
    last_digest = _state(conn, "digest_day") if conn else None
    while True:
        try:
            r = poll_once(mb, drafter, conn, firm, rules, log)
            if r.seen or r.sent_captured:
                log(f"[gmail] pass: {r}")
            today = datetime.now().date().isoformat()
            if datetime.now().hour >= 8 and last_digest != today:
                if digest_draft(mb, conn, firm):
                    log("[gmail] digest drafted for the manager")
                last_digest = _state(conn, "digest_day", today)
        except KeyboardInterrupt:
            raise
        except Exception as e:   # a network blip must not stop the watcher; mail is never dropped
            log(f"[gmail] pass failed, will retry: {e}")
        time.sleep(every)
