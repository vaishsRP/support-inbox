"""The public demo: a visitor writes to a made-up company, then switches to the
support team's side to see what the tool did with it.

Each browser gets a session id. Everything a visitor creates (their mails, drafts,
action rows, incident notes) is scoped to that id, so visitors never see each
other's messages. What a visitor "sends" is diffed and tracked like real sent
mail, but never joins the shared answer pool, so one visitor cannot teach the
demo something that another visitor then gets drafted.

Nothing here sends email. "Send" in the demo records the text, as the sent-folder
poll would in a real deployment.
"""

from __future__ import annotations

import re
import secrets
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from . import actions, docs, feedback
from .categories import SpikeConfig, assign, detect_spikes
from .embed import encode

MAX_MAILS_PER_SESSION = 25
NOTE_HOURS = 24
_SID = re.compile(r"^[A-Za-z0-9_-]{8,40}$")

INCIDENT = [
    "the heating in my room is off again, it's so cold",
    "no heating in block C since this morning??",
    "Radiator is completely cold, room 3.14",
    "heating not working, can someone come today",
    "Is the heating broken in the whole building? my room is freezing",
    "verwarming doet het niet in blok C",
    "no heat and no hot water on floor 2",
    "It's 14 degrees in my room, the heating stopped working",
    "heating off in block C, my neighbours have the same problem",
    "Our whole corridor has no heating since 8am",
    "radiators cold in block C, please help",
    "Die Heizung funktioniert nicht, Block C",
]


class SendIn(BaseModel):
    sid: str
    name: str = Field("", max_length=80)
    subject: str = Field("", max_length=160)
    body: str = Field(..., min_length=2, max_length=3000)
    reply_to: int | None = None      # draft id of the mail this one follows up on


class SentIn(BaseModel):
    sid: str
    draft_id: int
    text: str = Field(..., max_length=6000)


class NoteIn(BaseModel):
    sid: str
    title: str = Field(..., min_length=2, max_length=120)
    body: str = Field(..., min_length=5, max_length=3000)


class SidIn(BaseModel):
    sid: str


def _check(sid: str) -> str:
    if not _SID.match(sid or ""):
        raise HTTPException(400, "bad session")
    return sid


def mount(app: FastAPI, firm, db, drafter, rules) -> None:
    lock = threading.Lock()   # one draft at a time: the free model tier is rate-limited anyway
    page = (Path(__file__).parent / "static" / "demo.html").read_text(encoding="utf-8")

    @app.get("/demo", response_class=HTMLResponse)
    def demo_page():
        return HTMLResponse(page.replace("{{FIRM}}", firm.name.split(" (")[0]))

    @app.get("/demo/api/session")
    def new_session():
        return {"sid": secrets.token_urlsafe(12)}

    def _customer(sid: str) -> str:
        return f"demo-{sid}"

    def _mail_row(c: sqlite3.Connection, r) -> dict:
        m = c.execute("SELECT text FROM messages WHERE id = ?", (r["customer_msg_id"],)).fetchone()
        subject, _, body = (m["text"] if m else "").partition("\n\n")
        return {
            "id": r["id"], "thread_id": r["thread_id"], "at": r["created_at"], "route": r["route"],
            "confidence": r["confidence"], "reason": r["reason"], "draft": r["draft_text"],
            "subject": subject, "body": body, "sent": r["sent_text"], "outcome": r["outcome"],
            "edit_similarity": r["edit_similarity"], "sources": r["sources"],
        }

    @app.get("/demo/api/inbox")
    def inbox(sid: str):
        _check(sid)
        c = db()
        rows = c.execute(
            "SELECT * FROM drafts WHERE customer_id = ? ORDER BY id DESC", (_customer(sid),)
        ).fetchall()
        out = [_mail_row(c, r) for r in rows]
        c.close()
        return out

    @app.post("/demo/api/send")
    def send(m: SendIn):
        sid = _check(m.sid)
        from .draft import Incoming, result_json

        c = db()
        n = c.execute("SELECT COUNT(*) FROM drafts WHERE customer_id = ?", (_customer(sid),)).fetchone()[0]
        if n >= MAX_MAILS_PER_SESSION:
            c.close()
            raise HTTPException(429, "This demo session has reached its limit. Reload the page for a new one.")
        thread_id, thread = f"demo-{sid}-{n}", []
        if m.reply_to:
            prev = c.execute(
                "SELECT * FROM drafts WHERE id = ? AND customer_id = ?", (m.reply_to, _customer(sid))
            ).fetchone()
            if prev:
                thread_id = prev["thread_id"]
                pm = c.execute("SELECT text FROM messages WHERE id = ?", (prev["customer_msg_id"],)).fetchone()
                thread.append(("customer", pm["text"] if pm else ""))
                if prev["sent_text"]:
                    thread.append(("firm", prev["sent_text"]))
        now = datetime.now(timezone.utc)
        msg_id = f"demo-{sid}-{n}-{secrets.token_hex(3)}"
        text = (m.subject.strip() + "\n\n" + m.body.strip()).strip()
        with c:
            c.execute("INSERT INTO messages VALUES (?,?,?,?,1,?,?)", (msg_id, thread_id, None, _customer(sid), now.isoformat(), text))
        c.close()
        d = drafter()
        with lock:
            res = d.handle(Incoming(msg_id, thread_id, _customer(sid), m.body, now, thread, owner=sid))
        out = result_json(res)
        c = db()
        row = c.execute("SELECT * FROM drafts WHERE customer_msg_id = ?", (msg_id,)).fetchone()
        mail = _mail_row(c, row)
        c.close()
        return {"mail": mail, "result": out}

    @app.post("/demo/api/sent")
    def sent(s: SentIn):
        sid = _check(s.sid)
        c = db()
        row = c.execute("SELECT customer_id FROM drafts WHERE id = ?", (s.draft_id,)).fetchone()
        if not row or row["customer_id"] != _customer(sid):
            c.close()
            raise HTTPException(404, "no such mail in this session")
        out = feedback.record_sent(c, s.draft_id, s.text, datetime.now(timezone.utc), rules, firm.public_numbers, add_to_pool=False)
        c.close()
        return out

    @app.get("/demo/api/actions")
    def list_actions(sid: str):
        _check(sid)
        c = db()
        rows = c.execute(
            "SELECT * FROM actions WHERE (customer_id = ? OR rule = ?) ORDER BY status, due_at IS NULL, due_at, id DESC",
            (_customer(sid), f"spike:demo-{sid}"),
        ).fetchall()
        c.close()
        return [dict(r) for r in rows]

    @app.post("/demo/api/actions/{action_id}/done")
    def action_done(action_id: int, s: SidIn):
        sid = _check(s.sid)
        c = db()
        with c:
            c.execute(
                "UPDATE actions SET status='done', done_at=? WHERE id=? AND (customer_id=? OR rule=?)",
                (datetime.now(timezone.utc).isoformat(), action_id, _customer(sid), f"spike:demo-{sid}"),
            )
        c.close()
        return {"ok": True}

    @app.get("/demo/api/context")
    def context(sid: str):
        _check(sid)
        c = db()
        rows = [
            dict(r) for r in docs.live_docs(c)
            if r["owner"] is None or r["owner"] == sid
        ]
        c.close()
        return [{"id": r["id"], "kind": r["kind"], "title": r["title"], "body": r["body"],
                 "created_at": r["created_at"], "expires_at": r["expires_at"]} for r in rows]

    @app.post("/demo/api/note")
    def add_note(n: NoteIn):
        sid = _check(n.sid)
        c = db()
        count = c.execute("SELECT COUNT(*) FROM docs WHERE owner = ?", (sid,)).fetchone()[0]
        if count >= 5:
            c.close()
            raise HTTPException(429, "Five notes per demo session.")
        with c:
            doc_id = docs.add_doc(c, "note", n.title, n.body, expires_in_days=1, owner=sid)
        c.close()
        drafter().refresh_docs()
        return {"id": doc_id}

    @app.post("/demo/api/incident")
    def incident(s: SidIn):
        """Twelve made-up students write about the same outage within two hours.
        They are counted, not drafted (that would spend the free model quota), and the
        spike check runs exactly as it would on a live inbox."""
        sid = _check(s.sid)
        from .categories import load

        cats, centroids = load(firm)
        names = {c.id: c.name for c in cats}
        now = datetime.now(timezone.utc)
        vecs = encode(INCIDENT)
        cat_ids = assign(vecs, centroids)
        c = db()
        # History: the corpus, shifted so its last day is a week ago, gives each category a normal rate.
        hist = pd.read_sql_query("SELECT customer_id, asked_at, question FROM pairs WHERE source='corpus'", c)
        hist_vecs = encode(hist.question.tolist())
        hist["category"] = assign(hist_vecs, centroids)
        ts = pd.to_datetime(hist.asked_at, utc=True)
        hist["ts"] = ts - (ts.max() - (now - timedelta(days=7)))
        burst = pd.DataFrame(
            {
                "customer_id": [f"sim-{sid}-{i}" for i in range(len(INCIDENT))],
                "ts": [now - timedelta(minutes=9 * i) for i in range(len(INCIDENT))],
                "category": cat_ids,
            }
        )
        events = pd.concat([hist[["customer_id", "ts", "category"]], burst])
        spikes = detect_spikes(events, now, SpikeConfig(window_hours=2, min_customers=6))
        new = []
        with c:
            for sp in spikes:
                # scope the spike row to this visitor
                key = f"spike:demo-{sid}"
                if c.execute("SELECT 1 FROM actions WHERE rule=? AND status='open'", (key,)).fetchone():
                    continue
                name = names.get(sp["category"], str(sp["category"]))
                new.append(
                    actions.add(
                        c, "spike",
                        f"{sp['customers']} students wrote about \"{name}\" in the last 2 hours "
                        f"(normal: about {sp['normal']}). Consider a dated note on the context page.",
                        rule=key, origin="spike",
                    )
                )
        c.close()
        return {"messages": INCIDENT, "categories": [names.get(int(i), str(i)) for i in cat_ids], "spikes": len(spikes), "new_rows": new}
