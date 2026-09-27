"""Steps 4 to 6: turn one incoming mail into a draft, or a refusal with a reason.

Routes, in order:
  blocked  an incoming rule matched (legal threat, data request, ...): no draft at all
  reuse    a past question is close enough: adapt its approved answer
  docs     a document or dated note is close enough: draft from it and cite it
  refused  nothing close: no reply text, but pointers and questions for the agent
  failed   the model was unavailable or returned garbage: no draft, stated reason

Whatever the route, the outgoing rules then replace any commitment beyond authority
with a placeholder, and the draft opens with a review line. Nothing here sends.
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

from . import actions
from .config import Firm
from .docs import DocHit, DocIndex
from .embed import encode
from .llm import BadOutput, ChatModel, ModelUnavailable, chat_json
from .mailtext import customer_name, format_reply, language
from .retrieve import AnswerIndex, Hit
from .rules import Match, Rule, apply_placeholders, check_incoming, sentences
from .textclean import clean, redact

REVIEW_LINE = "[[REVIEW: delete this line once you have read the draft]]"

# Among past answers this close to the best one, the newest wins: policies change, and
# an old reply should not beat a new one on a hair of similarity.
RECENCY_BAND = 0.02
# Facts that change when a policy changes: amounts, durations, percentages.
_FACT = re.compile(
    r"(?:(?:€|\beur\b)\s?\d+(?:[.,]\d+)?|\b\d+(?:[.,]\d+)?\s?(?:working days|business days|werkdagen|days?|dagen|weeks?|weken|"
    r"months?|maanden|hours?|uur|minutes?|eur(?:os?)?|%|percent))",
    re.I,
)


def facts(text: str) -> set[str]:
    return {re.sub(r"\s+", " ", f.lower()) for f in _FACT.findall(text or "")}

# Past answers that describe a situation rather than a policy go stale. Found in the
# American Airlines replay: "we fully expect to avoid cancellations" reused weeks later.
STATUS_CLAIM_DAYS = 3
_STATUS_CLAIM = re.compile(
    r"\b(we(?:'re| are) aware|known issue|currently|right now|at the moment|at this time|"
    r"all should be (?:good|fine|back)|(?:we|we're|we are) (?:fully )?expect\w*|aren't expecting|"
    r"is (?:down|back up|working again)|(?:outage|disruption)s?\b|until it's fixed|"
    r"latest (?:version|update|software))",
    re.I,
)


@dataclass
class Thresholds:
    """Cosine similarity cut-offs. Set from hand-labelled pairs (step 3); until then
    the firm config marks them provisional and the report says so."""

    reuse: float
    docs: float
    high: float
    medium: float
    provisional: bool = True

    @classmethod
    def from_firm(cls, firm: Firm) -> "Thresholds":
        t = firm.raw.get("thresholds", {})
        return cls(
            reuse=float(t.get("reuse", 0.93)),
            docs=float(t.get("docs", 0.86)),
            high=float(t.get("high", 0.96)),
            medium=float(t.get("medium", 0.94)),
            provisional=bool(t.get("provisional", True)),
        )

    def band(self, sim: float) -> str:
        return "high" if sim >= self.high else "medium" if sim >= self.medium else "low"


@dataclass
class Incoming:
    msg_id: str
    thread_id: str
    customer_id: str
    text: str
    received_at: datetime
    # Earlier messages in this thread, oldest first: ("customer" | "firm", text)
    thread: list[tuple[str, str]] = field(default_factory=list)
    # Public demo only: whose private notes may be used. None = team-wide notes only.
    owner: str | None = None
    # Real email: who wrote it and what they attached (attachments are not read).
    sender_name: str = ""
    sender_addr: str = ""
    attachments: list[str] = field(default_factory=list)


@dataclass
class Result:
    route: str
    confidence: str | None
    draft: str | None
    reason: str
    notes: list[str] = field(default_factory=list)      # for the dashboard, never in the draft
    inline: list[str] = field(default_factory=list)     # things the agent must act on: go in the draft
    sources: list[dict] = field(default_factory=list)
    matches: list[Match] = field(default_factory=list)
    labels: list[str] = field(default_factory=list)
    best_sim: float | None = None


_EN_WORDS = set(
    "the a an and or to of in on for is are was were be been it this that you your we our i my me "
    "not no can could would will with at from have has had do does did but so if please thanks thank "
    "what when where why how just get got im i'm it's don't".split()
)


def looks_english(text: str) -> bool:
    words = re.findall(r"[a-zA-Z']+", text.lower())
    if len(words) < 4:
        return True
    return sum(w in _EN_WORDS for w in words) / len(words) >= 0.12


# ---- Prompts ---------------------------------------------------------------------

REUSE_SYSTEM = """You adapt an approved customer-support reply so it answers a new customer message.

Rules:
- Use only facts found in the APPROVED REPLY, the NEW MESSAGE or the THREAD. Never invent policies, amounts, dates, names, phone numbers or links.
- Keep the approved reply's wording and tone. Change only what the new message requires.
- The APPROVED REPLY was written on an earlier date for another customer. Remove anything tied to that moment or person that is not true TODAY for this customer: a month or season ("in February" when today is in September), "this morning", their room number, their dates.
- Double-bracket placeholders like [[name]] stand for another customer's details that were removed. Fill one only if the new message gives that detail; otherwise leave the placeholder exactly as it is.
- If the answer depends on something only the company's own systems know (payment, order, booking, account or delivery status), put [[CHECK: what the agent should look up]] where that fact would go.
- If the new message asks something the approved reply does not answer, do not answer it. List it under "uncovered".
- Do not promise refunds, money, compensation, exceptions, escalations or deadlines beyond what the approved reply already says.
- Write in the language of the new message. Keep it about as short as the approved reply.
- {framing}
- CUSTOMER'S EARLIER CONVERSATIONS, if given, show what this customer asked and was told before, with dates. Use them to keep the story consistent, to acknowledge when they are writing again about the same thing, and never to repeat word for word what they were already told. Do not present old facts as current unless the APPROVED REPLY confirms them.
- Decide "fits" first, strictly. It is true only if the approved reply deals with the same specific problem as the new message (same product or service, same kind of fault or request), so that a support agent would send it with small edits. Sharing a topic is not enough: a check-in app error and a passport kiosk are both "check-in" but not the same problem. If in doubt, false. When "fits" is false, leave "reply" empty.

Return JSON only: {"fits": true|false, "reply": "...", "uncovered": ["..."]}"""

DOCS_SYSTEM = """You write a short customer-support reply using only the SOURCES given.

Rules:
- Every fact must come from the SOURCES. List the titles of the sources you used under "used"; do not put source names or citations in the reply itself.
- If the SOURCES do not answer the message, set "fits" to false and leave "reply" empty.
- If the answer depends on something only the company's systems know, write [[CHECK: what to look up]] instead of guessing.
- Parts of the message the sources do not answer go under "uncovered", not in the reply.
- Do not promise refunds, money, compensation, exceptions, escalations or deadlines.
- CUSTOMER'S EARLIER CONVERSATIONS, if given, show what this customer asked and was told before, with dates. Keep the story consistent and acknowledge when they are writing again about the same thing; do not present old facts as current unless the SOURCES confirm them.
- Write like the TEAM'S RECENT REPLIES: same tone, same length, same way of addressing people. Do not copy their facts.
- Write in the language of the message. Two to four sentences.
- {framing}

Return JSON only: {"fits": true|false, "reply": "...", "uncovered": ["..."], "used": ["..."]}"""

REFUSE_SYSTEM = """A customer-support message has no approved answer and no document that covers it. Do NOT write a reply to the customer.

Help the agent instead: give up to three short questions the agent could ask the customer to pin the problem down, and name the kind of issue in a few words.

Return JSON only: {"questions": ["..."], "issue": "..."}"""

TRANSLATE_SYSTEM = """Translate each numbered sentence to English, literally. Keep [[...]] placeholders unchanged.
Return JSON only: {"english": ["...", "..."]} with exactly one entry per input sentence, in order."""


def _thread_block(thread: list[tuple[str, str]]) -> str:
    if not thread:
        return "(none)"
    return "\n".join(f"{who.upper()}: {text}" for who, text in thread[-6:])


# ---- The drafter -----------------------------------------------------------------


BODY_ONLY = "Write only the body: no greeting line and no sign-off, they are added separately."
WITH_FRAME = "No signature."


class Drafter:
    def __init__(
        self,
        firm: Firm,
        conn: sqlite3.Connection,
        model: ChatModel,
        rules: list[Rule],
        answers: AnswerIndex | None = None,
        thresholds: Thresholds | None = None,
    ):
        self.firm = firm
        self.conn = conn
        self.model = model
        self.rules = rules
        self.answers = answers if answers is not None else AnswerIndex(firm)
        self.t = thresholds or Thresholds.from_firm(firm)
        self._doc_index: DocIndex | None = None
        self._doc_index_day: str | None = None
        self.run: str | None = None   # set by a replay so its drafts stay apart from live ones
        self.style = firm.raw.get("email_style")   # None for tweet corpora: no greeting or sign-off
        framing = BODY_ONLY if self.style else WITH_FRAME
        self.reuse_system = REUSE_SYSTEM.replace("{framing}", framing)
        self.docs_system = DOCS_SYSTEM.replace("{framing}", framing)

    def _docs(self, at: datetime) -> DocIndex:
        day = at.date().isoformat()
        if self._doc_index is None or self._doc_index_day != day:
            self._doc_index = DocIndex(self.conn, at=at)
            self._doc_index_day = day
        return self._doc_index

    def refresh_docs(self) -> None:
        self._doc_index = None

    # -- main entry --
    def handle(self, mail: Incoming, persist: bool = True) -> Result:
        question = clean(mail.text)
        blocked = [m for m in check_incoming(question, self.rules) if m.action == "block"]
        if blocked:
            who = sorted({m.authority for m in blocked if m.authority})
            res = Result(
                route="blocked",
                confidence=None,
                draft=None,
                reason=f"{', '.join(m.rule.replace('_', ' ') for m in blocked)}: route to {', '.join(who) or 'a lead'}",
                matches=blocked,
                labels=["AI/needs-approval"],
            )
            return self._finish(mail, res, persist)

        vec = encode([question])[0]
        sent_here = [redact(clean(t), self.firm.public_numbers) for who, t in mail.thread if who == "firm"]
        hits = self.answers.search(vec=vec, k=6, before=mail.received_at, exclude_answers=sent_here, owner=mail.owner)
        if hits:
            near = [h for h in hits if h.sim >= hits[0].sim - RECENCY_BAND]
            newest = max(near, key=lambda h: h.asked_at)
            hits = [newest] + [h for h in hits if h is not newest]
        doc_hits = self._docs(mail.received_at).search(vec=vec, k=3, owner=mail.owner)
        best = hits[0].sim if hits else 0.0
        best_doc = doc_hits[0].sim if doc_hits else 0.0

        # A live dated note describes what is happening now, so when one matches it wins
        # over an older approved answer that only looks similar.
        note_first = bool(doc_hits) and doc_hits[0].kind == "note" and best_doc >= self.t.docs
        try:
            if note_first:
                res = self._from_docs(mail, question, doc_hits)
                if res is None and hits and best >= self.t.reuse:
                    res = self._reuse(mail, question, hits[0], hits)
            elif hits and best >= self.t.reuse:
                res = self._reuse(mail, question, hits[0], hits)
                if res is None and best_doc >= self.t.docs:
                    res = self._from_docs(mail, question, doc_hits)
            elif best_doc >= self.t.docs:
                res = self._from_docs(mail, question, doc_hits)
            else:
                res = None
            if res is None:
                res = self._refuse(mail, question, hits, doc_hits, best, best_doc)
        except ModelUnavailable as e:
            res = Result("failed", None, None, f"model unavailable: {e}", labels=["AI/no-answer"])
        except BadOutput as e:
            res = Result("failed", None, None, f"generation failed: {e}", labels=["AI/no-answer"])
        res.best_sim = best
        self._add_history_note(mail, res)
        return self._finish(mail, res, persist)

    # -- routes --
    def _reuse(self, mail: Incoming, question: str, top: Hit, hits: list[Hit]) -> Result | None:
        user = (
            f"TODAY: {mail.received_at:%A %d %B %Y}\n\n"
            f"NEW MESSAGE:\n{question}\n\nTHREAD SO FAR:\n{_thread_block(mail.thread)}\n\n"
            f"{self._customer_context(mail)}"
            f"APPROVED REPLY (written on {top.asked_at[:10]}, to a similar earlier message: \"{top.question}\"):\n{top.answer}"
        )
        out = chat_json(self.model, self.reuse_system, user)
        if not out.get("fits", True) or not str(out.get("reply", "")).strip():
            return None
        uncovered = [u for u in out.get("uncovered", []) if str(u).strip()]
        confidence = self.t.band(top.sim)
        notes = [f"closest past answer is from {top.asked_at[:10]} (similarity {top.sim:.2f})"]
        inline = []
        age_days = (mail.received_at - datetime.fromisoformat(top.asked_at)).days
        if age_days >= STATUS_CLAIM_DAYS and _STATUS_CLAIM.search(top.answer):
            # "We fully expect to avoid cancellations" was true on the day it was written.
            inline.append(
                f"the past answer describes the situation on {top.asked_at[:10]} ({age_days} days ago); "
                "check it is still true before sending"
            )
            confidence = "medium" if confidence == "high" else confidence
        if uncovered:
            confidence = "medium" if confidence == "high" else confidence
            inline += [f"not answered yet: {u}" for u in uncovered]
        changed = self._policy_change(top, hits)
        if changed:
            inline.append(changed)
            confidence = "medium" if confidence == "high" else confidence
        return Result(
            route="reuse",
            confidence=confidence,
            draft=str(out["reply"]).strip(),
            reason="adapted a past approved answer",
            notes=notes,
            inline=inline,
            sources=[{"type": "past_answer", "pair_id": h.pair_id, "sim": round(h.sim, 4)} for h in hits],
            labels=["AI/draft-ready"],
        )

    def _from_docs(self, mail: Incoming, question: str, doc_hits: list[DocHit]) -> Result | None:
        src = "\n\n".join(
            f"[{h.title}] ({'dated note, ' + h.created_at[:10] if h.kind == 'note' else 'standing document'})\n{h.text}"
            for h in doc_hits
        )
        tone = self._tone_examples()
        user = (
            f"TODAY: {mail.received_at:%A %d %B %Y}\n\n"
            f"MESSAGE:\n{question}\n\nTHREAD SO FAR:\n{_thread_block(mail.thread)}\n\nSOURCES:\n{src}"
            + (f"\n\nTEAM'S RECENT REPLIES (for tone only):\n{tone}" if tone else "")
            + (f"\n\n{self._customer_context(mail).strip()}" if self._customer_context(mail) else "")
        )
        out = chat_json(self.model, self.docs_system, user)
        if not out.get("fits", True) or not str(out.get("reply", "")).strip():
            return None
        uncovered = [u for u in out.get("uncovered", []) if str(u).strip()]
        used = [str(u) for u in out.get("used", []) if str(u).strip()]
        notes = ["drafted from documents, not a past answer" + (f": {', '.join(used)}" if used else "")]
        inline = [f"not answered yet: {u}" for u in uncovered]
        conf = "medium" if doc_hits[0].sim >= self.t.high and not uncovered else "low"
        return Result(
            route="docs",
            confidence=conf,
            draft=str(out["reply"]).strip(),
            reason="drafted from documents and notes",
            notes=notes,
            inline=inline,
            sources=[{"type": h.kind, "doc_id": h.doc_id, "title": h.title, "sim": round(h.sim, 4)} for h in doc_hits],
            labels=["AI/draft-ready"],
        )

    def _refuse(self, mail, question, hits, doc_hits, best, best_doc) -> Result:
        tried = []
        if hits and best >= self.t.reuse:
            tried.append("the closest past answer is about a different problem")
        if doc_hits and best_doc >= self.t.docs:
            tried.append("the closest documents do not cover it")
        if tried:
            reason = "nothing that answers it: " + " and ".join(tried)
        elif hits or doc_hits:
            reason = (
                f"nothing close enough: best past answer {best:.2f} (needs {self.t.reuse:.2f}), "
                f"best document {best_doc:.2f} (needs {self.t.docs:.2f})"
            )
        else:
            reason = "no past answers or documents to draw on"
        notes = []
        for h in hits[:3]:
            notes.append(f"related past thread {h.thread_id} ({h.sim:.2f}): \"{h.question[:120]}\"")
        for d in doc_hits[:2]:
            notes.append(f"related document \"{d.title}\" ({d.sim:.2f})")
        try:
            out = chat_json(self.model, REFUSE_SYSTEM, f"MESSAGE:\n{question}\n\nTHREAD SO FAR:\n{_thread_block(mail.thread)}", max_tokens=300)
            if out.get("issue"):
                notes.append(f"looks like: {out['issue']}")
            notes += [f"worth asking the customer: {q}" for q in out.get("questions", [])[:3] if str(q).strip()]
        except (ModelUnavailable, BadOutput):
            notes.append("could not suggest questions: model unavailable")
        return Result(
            route="refused",
            confidence=None,
            draft=None,
            reason=reason,
            notes=notes,
            sources=[{"type": "past_answer", "pair_id": h.pair_id, "sim": round(h.sim, 4)} for h in hits],
            labels=["AI/no-answer"],
        )

    # -- shared finishing --
    def _translator(self, text: str):
        """For non-English drafts: translate sentence by sentence so the English
        rules can see commitments, and map hits back to the original sentences."""
        if looks_english(text):
            return None
        sents = sentences(text)
        numbered = "\n".join(f"{i + 1}. {s}" for i, s in enumerate(sents))
        out = chat_json(self.model, TRANSLATE_SYSTEM, numbered, max_tokens=900)
        english = out.get("english", [])
        if len(english) != len(sents):
            raise BadOutput("translation did not line up with the sentences")
        table = dict(zip(sents, english))
        return lambda s: table.get(s, s)

    def _policy_change(self, top: Hit, hits: list[Hit]) -> str | None:
        """Older replies to the same kind of question that state different facts
        (amounts, durations) mean the policy may have changed. The newest is used; the
        agent is told what changed and when."""
        mine = facts(top.answer)
        for h in hits[1:]:
            if h.sim < top.sim - 0.05 or h.asked_at >= top.asked_at:
                continue
            theirs = facts(h.answer)
            if mine and theirs and mine != theirs:
                return (
                    f"earlier replies said {', '.join(sorted(theirs - mine) or sorted(theirs))} ({h.asked_at[:10]}); "
                    f"the newest says {', '.join(sorted(mine - theirs) or sorted(mine))} ({top.asked_at[:10]}). "
                    "Used the newest: check it is the current policy"
                )
        return None

    def _customer_context(self, mail: Incoming, n: int = 3) -> str:
        """This customer's earlier conversations (other threads), dated, oldest first."""
        past = actions.customer_history(self.conn, mail.customer_id, before=mail.received_at.isoformat(), limit=n + 2)
        past = [p for p in past if p["thread_id"] != mail.thread_id][:n]
        if not past:
            return ""
        lines = [
            f"- {p['asked_at'][:10]}: they wrote \"{p['question'][:220]}\"; the team replied \"{p['answer'][:260]}\""
            for p in reversed(past)
        ]
        return "CUSTOMER'S EARLIER CONVERSATIONS (oldest first):\n" + "\n".join(lines) + "\n\n"

    def _tone_examples(self, n: int = 2) -> str:
        """The team's most recent real replies, so a draft written from documents still
        sounds like them. Replies the team actually sent come first."""
        rows = self.conn.execute(
            "SELECT answer FROM pairs WHERE is_deflection = 0 AND retired = 0 "
            "ORDER BY source = 'sent' DESC, answered_at DESC LIMIT ?",
            (n,),
        ).fetchall()
        return "\n---\n".join(r[0] for r in rows)

    def _add_history_note(self, mail: Incoming, res: Result) -> None:
        past = actions.customer_history(self.conn, mail.customer_id, before=mail.received_at.isoformat(), limit=3)
        past = [p for p in past if p["thread_id"] != mail.thread_id]
        if past:
            last = past[0]
            res.notes.append(
                f"this customer wrote {len(past)}{'+' if len(past) == 3 else ''} time(s) before; "
                f"last on {last['asked_at'][:10]}: \"{last['question'][:100]}\""
            )

    def _finish(self, mail: Incoming, res: Result, persist: bool) -> Result:
        if res.draft:
            try:
                translate = self._translator(res.draft)
            except (ModelUnavailable, BadOutput) as e:
                # Cannot check commitments in a language we cannot read: hold the draft.
                res.notes.append(f"draft not in English and could not be translated for the commitment check ({e})")
                res.route, res.draft, res.confidence = "failed", None, None
                res.reason = "commitment check impossible without a translation"
                res.labels = ["AI/no-answer"]
                translate = None
            if res.draft:
                body, matches = apply_placeholders(res.draft, self.rules, translate=translate)
                res.matches += matches
                if any(m.action == "placeholder" for m in matches):
                    res.notes.append("commitments were replaced with NEEDS APPROVAL placeholders")
                for m in matches:
                    if m.action == "track":
                        res.notes.append(f"this draft promises something that will be tracked once sent: \"{m.sentence}\"")
                if mail.attachments:
                    res.inline.append(f"the customer attached {', '.join(mail.attachments)}; it was not read")
                name = customer_name(mail.text, mail.sender_name, mail.sender_addr)
                body = format_reply(body, self.style, name, language(mail.text))
                # Only what the agent must act on goes in the draft; the rest is on the dashboard.
                inline = "".join(f"\n\n[[AGENT NOTE: {n}]]" for n in res.inline)
                res.draft = f"{REVIEW_LINE}\n\n{body}{inline}".rstrip()
        res.labels = [final_label(res)]
        if persist:
            self._persist(mail, res)
        return res

    def _persist(self, mail: Incoming, res: Result) -> None:
        with self.conn:
            self.conn.execute(
                """INSERT INTO drafts (thread_id, customer_msg_id, customer_id, created_at, route, confidence,
                   reason, draft_text, source_pair_id, sources, run, notes)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    mail.thread_id,
                    mail.msg_id,
                    mail.customer_id,
                    mail.received_at.isoformat(),
                    res.route,
                    res.confidence,
                    res.reason + ("" if not res.notes or res.draft else " | " + " | ".join(res.notes)),
                    res.draft,
                    next((s["pair_id"] for s in res.sources if s.get("type") == "past_answer"), None),
                    json.dumps(res.sources),
                    self.run,
                    json.dumps(res.notes),
                ),
            )
            if res.draft and self.run is None:
                actions.record_checks(self.conn, res.draft, customer_id=mail.customer_id, thread_id=mail.thread_id)


def final_label(res: Result) -> str:
    """Exactly one label per email. Confidence is a sub-label of draft-ready, so an
    email never sits in two places at once."""
    if res.route == "blocked" or (res.draft and "[[NEEDS APPROVAL" in res.draft):
        return "AI/needs-approval"
    if res.draft and res.route in ("reuse", "docs"):
        return f"AI/draft-ready/{res.confidence or 'low'}"
    return "AI/no-answer"


def result_json(res: Result) -> dict:
    d = asdict(res)
    d["matches"] = [
        {"rule": m.rule, "action": m.action, "sentence": m.sentence, "due": m.due.isoformat() if m.due else None}
        for m in res.matches
    ]
    return d
