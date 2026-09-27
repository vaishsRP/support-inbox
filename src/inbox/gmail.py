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
LABELS = [
    "AI/draft-ready", "AI/needs-approval", "AI/no-answer",
    "AI/confidence-high", "AI/confidence-medium", "AI/confidence-low", "AI/automated", "AI/digest",
]

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
            full = self._svc.users().messages().get(userId="me", id=m["id"], format="raw").execute()
            mail = parse(base64.urlsafe_b64decode(full["raw"]))
            out.append({"id": m["id"], "labels": m.get("labelIds", []), "at": int(m["internalDate"]), "mail": mail})
        return sorted(out, key=lambda x: x["at"])

    # -- labels --
    def ensure_labels(self) -> None:
        existing = self._svc.users().labels().list(userId="me").execute().get("labels", [])
        self._label_ids = {l["name"]: l["id"] for l in existing}
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
        rep.seen += 1
        mail, meta = mb.get(msg_id)
        thread_id = meta["threadId"]
        received = datetime.fromtimestamp(int(meta["internalDate"]) / 1000, tz=timezone.utc)
        def skip(why: str) -> None:
            rep.skipped += 1
            with conn:
                conn.execute(
                    "INSERT OR IGNORE INTO gmail_threads (message_id, thread_id, draft_row, handled_at, status) "
                    "VALUES (?,?,NULL,?, 'closed')", (msg_id, thread_id, datetime.now(timezone.utc).isoformat()))
            log(f"[gmail] skipped {mail.subject!r}: {why}")

        if mail.from_addr == mb.me:
            skip("our own message")
            continue
        if mail.automated:
            mb.label(msg_id, ["AI/automated"])
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
