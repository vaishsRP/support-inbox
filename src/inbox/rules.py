"""The rule file: what incoming mail must never be drafted, what a draft must not
commit to, and which allowed commitments go on the action list.

Rules live in YAML so a support manager can read and edit them. The same rules
run on drafts (to replace commitments with placeholders) and on sent replies (to
catch commitments a human typed in themselves).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

import yaml

from .config import ROOT

DEFAULT_RULES = ROOT / "config" / "rules" / "default.yaml"
_SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\[\"'(])")


@dataclass
class Rule:
    id: str
    applies_to: str
    action: str
    patterns: list[re.Pattern]
    unless: list[re.Pattern] = field(default_factory=list)
    authority: str | None = None

    def hit(self, sentence: str) -> bool:
        if not any(p.search(sentence) for p in self.patterns):
            return False
        return not any(u.search(sentence) for u in self.unless)


@dataclass
class Match:
    rule: str
    action: str
    sentence: str
    authority: str | None = None
    due: datetime | None = None
    due_text: str | None = None   # the words the deadline came from, or why it is unknown


def load_rules(path: Path = DEFAULT_RULES) -> list[Rule]:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    out = []
    for r in raw["rules"]:
        if r["applies_to"] not in ("incoming", "outgoing"):
            raise ValueError(f"rule {r['id']}: applies_to must be incoming or outgoing")
        if r["action"] not in ("block", "placeholder", "track"):
            raise ValueError(f"rule {r['id']}: unknown action {r['action']}")
        if r["action"] == "block" and r["applies_to"] != "incoming":
            raise ValueError(f"rule {r['id']}: block only makes sense on incoming mail")
        out.append(
            Rule(
                id=r["id"],
                applies_to=r["applies_to"],
                action=r["action"],
                patterns=[re.compile(p, re.I) for p in r["patterns"]],
                unless=[re.compile(p, re.I) for p in r.get("unless", [])],
                authority=r.get("authority"),
            )
        )
    return out


def sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE.split(text or "") if s.strip()]


def check_incoming(text: str, rules: list[Rule]) -> list[Match]:
    hits = []
    for s in sentences(text):
        for r in rules:
            if r.applies_to == "incoming" and r.hit(s):
                hits.append(Match(r.id, r.action, s, r.authority))
    return hits


def check_outgoing(
    text: str,
    rules: list[Rule],
    sent_at: datetime | None = None,
    translate: Callable[[str], str] | None = None,
) -> list[Match]:
    """Find commitments in a draft or a sent reply.

    `translate`, when given, turns a non-English sentence into English so the
    English rules can see it. The match keeps the original sentence.
    """
    hits = []
    for s in sentences(text):
        probe = translate(s) if translate else s
        for r in rules:
            if r.applies_to != "outgoing" or not r.hit(probe):
                continue
            m = Match(r.id, r.action, s, r.authority)
            if r.action == "track":
                m.due, m.due_text = parse_due(probe, sent_at)
            hits.append(m)
            break  # one rule per sentence: the first, most serious one listed
    return hits


def apply_placeholders(text: str, rules: list[Rule], translate=None) -> tuple[str, list[Match]]:
    """Replace every sentence that commits beyond authority with a placeholder."""
    hits = check_outgoing(text, rules, translate=translate)
    blocked = [h for h in hits if h.action == "placeholder"]
    for h in blocked:
        who = f", ask {h.authority}" if h.authority else ""
        text = text.replace(h.sentence, f"[[NEEDS AUTHORITY: {h.rule.replace('_', ' ')}{who}]]", 1)
    return text, hits


# ---- Deadlines -------------------------------------------------------------------

_VAGUE = re.compile(r"\b(shortly|soon|asap|as soon as possible|in a few (days|hours)|in due course|promptly)\b", re.I)
_WITHIN = re.compile(
    r"\b(?:within|in|allow|up to)\s+(?:(\d+)\s*(?:-|to)\s*)?(\d+)\s+(business\s+)?(hour|day|week)s?\b", re.I
)
_WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
_DAY = re.compile(r"\b(today|tonight|tomorrow|" + "|".join(_WEEKDAYS) + r")\b", re.I)
_END_OF = re.compile(r"\bend of (the )?(day|week)\b", re.I)


def _add_business_days(start: datetime, n: int) -> datetime:
    d = start
    while n > 0:
        d += timedelta(days=1)
        if d.weekday() < 5:
            n -= 1
    return d


def parse_due(sentence: str, sent_at: datetime | None) -> tuple[datetime | None, str | None]:
    """When is a tracked commitment due? Returns (None, reason) if it cannot be said.

    Ranges take the later end: "7-10 business days" is due after 10.
    """
    if sent_at is None:
        return None, "no send time"
    m = _WITHIN.search(sentence)
    if m:
        n, business, unit = int(m.group(2)), bool(m.group(3)), m.group(4).lower()
        if unit == "hour":
            return sent_at + timedelta(hours=n), m.group(0)
        if unit == "week":
            return sent_at + timedelta(weeks=n), m.group(0)
        return (_add_business_days(sent_at, n) if business else sent_at + timedelta(days=n)), m.group(0)
    m = _END_OF.search(sentence)
    if m:
        if m.group(2).lower() == "day":
            return sent_at.replace(hour=23, minute=59), m.group(0)
        return (sent_at + timedelta(days=(4 - sent_at.weekday()) % 7)).replace(hour=23, minute=59), m.group(0)
    m = _DAY.search(sentence)
    if m:
        word = m.group(1).lower()
        if word in ("today", "tonight"):
            return sent_at.replace(hour=23, minute=59), m.group(0)
        if word == "tomorrow":
            return (sent_at + timedelta(days=1)).replace(hour=23, minute=59), m.group(0)
        ahead = (_WEEKDAYS.index(word) - sent_at.weekday()) % 7 or 7
        return (sent_at + timedelta(days=ahead)).replace(hour=23, minute=59), m.group(0)
    m = _VAGUE.search(sentence)
    if m:
        return None, f"vague: {m.group(0)}"
    return None, "no deadline stated"
