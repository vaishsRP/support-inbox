"""The service: two screens and a small JSON API. Nothing in here can send mail.

  /          the action list: commitments, checks, spikes; plus frequent complaints
  /context   the context page: standing documents, dated notes, retiring answers
  /api/...   process a mail, record a sent reply, digest, measures

Listens on localhost only. A real deployment needs a login in front of it (see the
spec's "before any real deployment").
"""

from __future__ import annotations

import html
import sqlite3
from datetime import datetime, timezone
from urllib.parse import parse_qs

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from pydantic import BaseModel, Field

from . import actions, docs, feedback
from .config import Firm, load_firm
from .rules import load_rules
from .store import connect


class MailIn(BaseModel):
    msg_id: str
    thread_id: str
    customer_id: str
    text: str
    received_at: datetime | None = None
    thread: list[tuple[str, str]] = Field(default_factory=list)


class SentIn(BaseModel):
    draft_id: int
    text: str
    sent_at: datetime | None = None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _e(s) -> str:
    return html.escape("" if s is None else str(s))


CSS = """
:root{--bg:#fbfaf8;--fg:#1d1d1b;--muted:#6b6962;--line:#e4e1da;--accent:#2f5d50;--warn:#a1461c;--card:#fff}
@media (prefers-color-scheme:dark){:root{--bg:#161614;--fg:#ecebe6;--muted:#a09d95;--line:#33322e;--accent:#8cc3b0;--warn:#f0a07a;--card:#1e1e1b}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,-apple-system,Segoe UI,sans-serif}
main{max-width:980px;margin:0 auto;padding:24px 16px 64px}header{display:flex;gap:16px;align-items:baseline;flex-wrap:wrap;margin-bottom:8px}
h1{font-size:22px;margin:0}h2{font-size:16px;margin:28px 0 8px;color:var(--muted);text-transform:uppercase;letter-spacing:.04em}
nav a{color:var(--accent);margin-right:12px;text-decoration:none}nav a:hover{text-decoration:underline}
.row{display:flex;gap:12px;align-items:flex-start;padding:10px 12px;border:1px solid var(--line);border-radius:8px;background:var(--card);margin-bottom:6px}
.row .what{flex:1;min-width:0;overflow-wrap:anywhere}.meta{color:var(--muted);font-size:13px}.late{color:var(--warn);font-weight:600}
button{font:inherit;border:1px solid var(--line);background:var(--card);color:var(--fg);border-radius:6px;padding:4px 10px;cursor:pointer}
button:hover{border-color:var(--accent)}table{border-collapse:collapse;width:100%}td,th{text-align:left;padding:6px 8px;border-bottom:1px solid var(--line)}
th{color:var(--muted);font-weight:500;font-size:13px}td.n{text-align:right;font-variant-numeric:tabular-nums}
input,textarea{font:inherit;width:100%;padding:8px;border:1px solid var(--line);border-radius:6px;background:var(--card);color:var(--fg)}
textarea{min-height:110px}form.add{display:grid;gap:8px;padding:12px;border:1px solid var(--line);border-radius:8px;background:var(--card)}
.empty{color:var(--muted);font-style:italic}.pill{font-size:12px;padding:1px 8px;border-radius:99px;border:1px solid var(--line);color:var(--muted)}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:8px}.stat{padding:10px 12px;border:1px solid var(--line);border-radius:8px;background:var(--card)}
.stat b{display:block;font-size:22px}
"""


def page(title: str, firm: Firm, body: str) -> HTMLResponse:
    return HTMLResponse(
        f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_e(title)}</title><style>{CSS}</style></head><body><main>
<header><h1>{_e(title)}</h1><span class="meta">{_e(firm.name)}</span></header>
<nav><a href="/">Action list</a><a href="/context">Context page</a><a href="/api/digest">Today's digest</a></nav>
{body}</main></body></html>"""
    )


async def _form(request: Request) -> dict[str, str]:
    raw = (await request.body()).decode("utf-8")
    return {k: v[0] for k, v in parse_qs(raw, keep_blank_values=True).items()}


def create_app(firm_key: str | None = None, firm: Firm | None = None, model=None, demo: bool = False) -> FastAPI:
    firm = firm or load_firm(firm_key or "americanair")
    app = FastAPI(title="Support inbox", docs_url="/api/docs")
    conn = connect(firm.db_path)
    conn.close()
    rules = load_rules()
    state: dict = {"drafter": None}

    def db() -> sqlite3.Connection:
        c = sqlite3.connect(firm.db_path, check_same_thread=False)
        c.row_factory = sqlite3.Row
        return c

    def drafter():
        if state["drafter"] is None:
            from .draft import Drafter
            from .llm import OpenAICompatible

            state["drafter"] = Drafter(firm, db(), model or OpenAICompatible(), rules)
        return state["drafter"]

    if demo:
        from .demo import mount

        mount(app, firm, db, drafter, rules)

    # ---- Action list --------------------------------------------------------------
    @app.get("/", response_class=HTMLResponse)
    def action_list():
        if demo:
            return RedirectResponse("/demo", status_code=307)
        c = db()
        now = _now()
        rows = actions.open_rows(c)
        done = c.execute("SELECT * FROM actions WHERE status='done' ORDER BY done_at DESC LIMIT 15").fetchall()
        m = feedback.measures(c)
        c.close()

        def item(r, button=True):
            late = r["due_at"] and r["due_at"] < now.isoformat()
            due = (
                f"<span class='{'late' if late else ''}'>due {_e(r['due_at'][:16].replace('T', ' '))}</span>"
                if r["due_at"]
                else _e(r["due_text"] or "")
            )
            who = f" · customer {_e(r['customer_id'])} · thread {_e(r['thread_id'])}" if r["customer_id"] else ""
            act = "done" if r["status"] == "open" else "reopen"
            btn = (
                f"<form method='post' action='/actions/{r['id']}/{act}'><button>{'Done' if act == 'done' else 'Reopen'}</button></form>"
                if button
                else ""
            )
            return (
                f"<div class='row'><div class='what'>{_e(r['what'])}<div class='meta'><span class='pill'>{_e(r['kind'])}</span> "
                f"{due}{who}</div></div>{btn}</div>"
            )

        groups = [
            ("Overdue promises", [r for r in rows if r["kind"] == "commitment" and r["due_at"] and r["due_at"] < now.isoformat()]),
            ("Promises coming up", [r for r in rows if r["kind"] == "commitment" and r["due_at"] and r["due_at"] >= now.isoformat()]),
            ("Promises with no clear deadline", [r for r in rows if r["kind"] == "commitment" and not r["due_at"]]),
            ("Checks to make", [r for r in rows if r["kind"] == "check"]),
            ("Spikes", [r for r in rows if r["kind"] == "spike"]),
        ]
        body = "".join(
            f"<h2>{t} ({len(items)})</h2>" + ("".join(item(r) for r in items) or "<p class='empty'>None.</p>")
            for t, items in groups
        )
        stats = (
            "<h2>How the tool is doing</h2><div class='grid'>"
            f"<div class='stat'><b>{m['mails']}</b>mails handled</div>"
            f"<div class='stat'><b>{'' if m['draft_rate'] is None else f'{100 * m['draft_rate']:.0f}%'}</b>got a draft</div>"
            f"<div class='stat'><b>{m['refused']}</b>refused, with a reason</div>"
            f"<div class='stat'><b>{'' if m['edit_similarity_median'] is None else f'{m['edit_similarity_median']:.2f}'}</b>median draft kept (1 = sent untouched)</div>"
            f"<div class='stat'><b>{m['placeholders_leaked']}</b>placeholders that reached a customer</div></div>"
            "<p class='meta'>An untouched draft can mean it was right or that nobody read it.</p>"
        )
        recent = "<h2>Recently done</h2>" + ("".join(item(r) for r in done) or "<p class='empty'>Nothing yet.</p>")
        return page("Action list", firm, body + stats + recent)

    @app.post("/actions/{action_id}/done")
    def done(action_id: int):
        c = db()
        with c:
            actions.mark_done(c, action_id)
        c.close()
        return RedirectResponse("/", status_code=303)

    @app.post("/actions/{action_id}/reopen")
    def reopen(action_id: int):
        c = db()
        with c:
            actions.reopen(c, action_id)
        c.close()
        return RedirectResponse("/", status_code=303)

    # ---- Context page -------------------------------------------------------------
    @app.get("/context", response_class=HTMLResponse)
    def context_page():
        c = db()
        live = docs.live_docs(c)
        expired = c.execute(
            "SELECT * FROM docs WHERE retired = 0 AND expires_at IS NOT NULL AND expires_at <= ? ORDER BY expires_at DESC LIMIT 10",
            (_now().isoformat(),),
        ).fetchall()
        c.close()

        def doc_row(d, retire=True):
            when = (
                f"note from {_e(d['created_at'][:10])}, expires {_e(d['expires_at'][:10])}"
                if d["kind"] == "note"
                else f"standing document, added {_e(d['created_at'][:10])}"
            )
            btn = f"<form method='post' action='/context/{d['id']}/retire'><button>Retire</button></form>" if retire else ""
            return (
                f"<div class='row'><div class='what'><b>{_e(d['title'])}</b><div class='meta'>{when}</div>"
                f"<div>{_e(d['body'][:400])}{'…' if len(d['body']) > 400 else ''}</div></div>{btn}</div>"
            )

        notes = [d for d in live if d["kind"] == "note"]
        standing = [d for d in live if d["kind"] == "standing"]
        body = f"""
<h2>Add a dated note</h2>
<form class="add" method="post" action="/context/note">
<input name="title" placeholder="What happened, in a few words" required>
<textarea name="body" placeholder="What changed, what customers will ask, and the workaround" required></textarea>
<label class="meta">Stops being used after <input name="days" type="number" value="14" min="1" max="365" style="width:90px"> days</label>
<button>Add note</button></form>
<h2>Current notes ({len(notes)})</h2>{''.join(doc_row(d) for d in notes) or "<p class='empty'>No live notes.</p>"}
<h2>Add a standing document</h2>
<form class="add" method="post" action="/context/doc">
<input name="title" placeholder="Title, e.g. Baggage policy" required>
<textarea name="body" placeholder="Paste the policy or help-centre page text" required></textarea>
<button>Add document</button></form>
<h2>Standing documents ({len(standing)})</h2>{''.join(doc_row(d) for d in standing) or "<p class='empty'>None yet.</p>"}
<h2>Recently expired notes</h2>{''.join(doc_row(d, False) for d in expired) or "<p class='empty'>None.</p>"}
<h2>Retire a past answer</h2>
<form class="add" method="post" action="/answers/retire">
<input name="pair_id" type="number" placeholder="Answer id (shown in the draft's sources)" required>
<button>Retire answer</button></form>
<p class="meta">A retired answer is never retrieved again: use it for answers from before a policy change, or answers that were wrong.</p>
"""
        return page("Context page", firm, body)

    def _refresh():
        if state["drafter"] is not None:
            state["drafter"].refresh_docs()

    @app.post("/context/note")
    async def add_note(request: Request):
        f = await _form(request)
        c = db()
        with c:
            docs.add_doc(c, "note", f["title"], f["body"], expires_in_days=int(f.get("days") or 14))
        c.close()
        _refresh()
        return RedirectResponse("/context", status_code=303)

    @app.post("/context/doc")
    async def add_standing(request: Request):
        f = await _form(request)
        c = db()
        with c:
            docs.add_doc(c, "standing", f["title"], f["body"])
        c.close()
        _refresh()
        return RedirectResponse("/context", status_code=303)

    @app.post("/context/{doc_id}/retire")
    def retire_doc(doc_id: int):
        c = db()
        with c:
            docs.retire_doc(c, doc_id)
        c.close()
        _refresh()
        return RedirectResponse("/context", status_code=303)

    @app.post("/answers/retire")
    async def retire_answer(request: Request):
        f = await _form(request)
        c = db()
        with c:
            c.execute("UPDATE pairs SET retired = 1 WHERE id = ?", (int(f["pair_id"]),))
        c.close()
        state["drafter"] = None  # rebuild the answer index without it
        return RedirectResponse("/context", status_code=303)

    # ---- API ---------------------------------------------------------------------
    @app.post("/api/mail")
    def process_mail(mail: MailIn):
        from .draft import Incoming, result_json

        d = drafter()
        received = mail.received_at or _now()
        with d.conn:
            # Keep the customer's message, so the reply sent later can join the answer pool.
            d.conn.execute(
                "INSERT OR IGNORE INTO messages VALUES (?,?,?,?,1,?,?)",
                (mail.msg_id, mail.thread_id, None, mail.customer_id, received.isoformat(), mail.text),
            )
        res = d.handle(Incoming(mail.msg_id, mail.thread_id, mail.customer_id, mail.text, received, list(mail.thread)))
        out = result_json(res)
        out["draft_id"] = d.conn.execute("SELECT MAX(id) FROM drafts").fetchone()[0]
        return out

    @app.post("/api/sent")
    def sent(s: SentIn):
        c = db()
        try:
            return feedback.record_sent(c, s.draft_id, s.text, s.sent_at or _now(), rules, firm.public_numbers)
        except KeyError as e:
            raise HTTPException(404, str(e))
        finally:
            c.close()

    @app.get("/api/digest", response_class=PlainTextResponse)
    def digest(at: datetime | None = None):
        c = db()
        text = actions.digest(c, now=at or _now(), firm_name=firm.name)
        c.close()
        return text

    @app.get("/api/measures")
    def measures():
        c = db()
        m = feedback.measures(c)
        c.close()
        return m

    @app.get("/api/actions")
    def list_actions():
        c = db()
        rows = [dict(r) for r in actions.open_rows(c)]
        c.close()
        return rows

    return app


def main() -> None:  # pragma: no cover
    import argparse
    import os

    import uvicorn

    ap = argparse.ArgumentParser()
    ap.add_argument("--firm", default=os.environ.get("INBOX_FIRM", "americanair"))
    ap.add_argument("--host", default=os.environ.get("INBOX_HOST", "127.0.0.1"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8000)))
    ap.add_argument("--demo", action="store_true", default=bool(os.environ.get("INBOX_DEMO")))
    a = ap.parse_args()
    uvicorn.run(create_app(a.firm, demo=a.demo), host=a.host, port=a.port)


if __name__ == "__main__":  # pragma: no cover
    main()
