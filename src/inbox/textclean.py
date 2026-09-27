"""Cleaning, deflection detection, and personal-detail redaction for message text.

Redaction matters most here. A stored approved answer contains the previous
customer's name, booking reference, phone number. Adapting it for a new customer
must never carry those across, so they are replaced with [[...]] placeholders at
import time, before anything is embedded or drafted from.
"""

from __future__ import annotations

import html
import re

PLACEHOLDER_RE = re.compile(r"\[\[[^\[\]]+\]\]")

_MENTION = re.compile(r"(?<![\w@])@\w+")
_URL = re.compile(r"https?://\S+")
# Agent initials at the very end: " ^MS", " /KB", " -JT", " *AB"
_SIGNOFF = re.compile(r"\s+[\^/*~-][A-Z]{1,3}\s*$")
_WS = re.compile(r"\s+")

_DEFLECTION = re.compile(
    r"\b(?:DMs?|direct message|private message|PM|send us a (?:message|note)|"
    r"contact (?:our|us|the)|reach (?:out|us)|get in touch|chat with us|"
    r"give (?:us|our [\w ]{1,40}?team) a call|call us|follow us|meet us in)\b",
    re.I,
)

_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]+\b")
_PHONE = re.compile(r"(?<!\d)(?:\+?\d{1,3}[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}(?!\d)")
_ORDER = re.compile(r"\b\d{3}-\d{7}-\d{7}\b")
_FLIGHT = re.compile(r"\b(flight|flt|fl)\s*#?\s*(\d{2,4})\b", re.I)
# Six-character booking codes with at least one digit and one letter: "XK4J2P"
_BOOKING = re.compile(r"\b(?=[A-Z0-9]{6}\b)(?=[A-Z0-9]*\d)(?=[A-Z0-9]*[A-Z])[A-Z0-9]{6}\b")
_CASE = re.compile(r"\b(case|ticket|reference|ref|order)\s*(?:number|no\.?|#)?\s*:?\s*#?(\d{5,})\b", re.I)
_LONG_DIGITS = re.compile(r"(?<![\w\]])\d{7,}(?![\w\[])")

_NAME_WORD = r"([A-ZÀ-ÖØ-Þ][a-zß-öø-ÿ]{1,14}(?:\s[A-Z]\.)?)"
_GREETING = re.compile(
    r"\b(Hi|Hey|Hello|Hiya|Howdy|Thanks|Thank you|Sorry|Aw+|Oh no|Welcome|Congrats|"
    r"Congratulations|Good morning|Good afternoon|Good evening|Happy to help|Help is here|"
    r"Help's here|Yes|No|Ok|Okay|Sure|Absolutely|Of course|Dear|Hoi|Hallo|Beste|Dag|Liebe|Lieber|Hola|Bonjour|Salut|Ciao|Olá),?\s+" + _NAME_WORD + r"(?=\s*[!.,?])"
)
# "..., John." or "..., Chris!" right before sentence end: a direct address.
_VOCATIVE = re.compile(r",\s" + _NAME_WORD + r"(?=\s*[!.?](?:\s|$))")
_NOT_NAMES = {
    "Please", "Thanks", "Thank", "Sorry", "There", "Again", "Today", "Tomorrow", "Yes",
    "Team", "Everyone", "Friend", "Folks", "All", "Guys", "Here", "Now", "Soon", "Too",
    "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday",
    "January", "February", "March", "April", "May", "June", "July", "August",
    "September", "October", "November", "December", "Apple", "American", "Siri",
    "iPhone", "Mac", "Settings", "General", "About", "Airlines", "Admirals",
}


def clean(text: str, keep_urls: bool = False) -> str:
    """Normalise a raw message: unescape HTML, drop handles and sign-offs.

    URLs become [link] in customer messages, but approved answers keep them
    (keep_urls=True): there the link is often the answer, a help article.
    """
    text = html.unescape(text or "")
    if not keep_urls:
        text = _URL.sub("[link]", text)
    text = _MENTION.sub(" ", text)
    text = _SIGNOFF.sub("", text)
    return _WS.sub(" ", text).strip()


def is_deflection(text: str) -> bool:
    """A reply that moves the conversation elsewhere instead of answering it."""
    return bool(_DEFLECTION.search(text or ""))


def redact(text: str, public_numbers: list[str] | tuple[str, ...] = ()) -> str:
    """Replace one customer's personal details with placeholders."""
    # The firm's own numbers and any links are shielded: a short-link code like
    # t.co/XK4J2P would otherwise look like a booking reference.
    shielded = list(public_numbers) + _URL.findall(text)
    keep = {n: f"\x00{i}\x00" for i, n in enumerate(shielded)}
    for n, token in keep.items():
        text = text.replace(n, token)

    text = _EMAIL.sub("[[email]]", text)
    text = _ORDER.sub("[[order number]]", text)
    text = _CASE.sub(lambda m: f"{m.group(1)} [[{m.group(1).lower()} number]]", text)
    text = _PHONE.sub("[[phone]]", text)
    text = _FLIGHT.sub(lambda m: f"{m.group(1)} [[flight number]]", text)
    text = _BOOKING.sub("[[booking reference]]", text)
    text = _LONG_DIGITS.sub("[[number]]", text)

    def _name(m: re.Match, group: int) -> str:
        word = m.group(group)
        if word.split()[0] in _NOT_NAMES:
            return m.group(0)
        return m.group(0).replace(word, "[[name]]", 1)

    text = _GREETING.sub(lambda m: _name(m, 2), text)
    text = _VOCATIVE.sub(lambda m: _name(m, 1), text)

    for n, token in keep.items():
        text = text.replace(token, n)
    return text


def placeholders(text: str) -> list[str]:
    return PLACEHOLDER_RE.findall(text or "")
