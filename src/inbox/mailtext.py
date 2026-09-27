"""Real email: reading it cleanly, and writing replies that look like email.

Reading (mail hygiene):
  parse()          raw RFC 822 message -> headers, plain-text body, attachment names
  strip_quoted()   drop the quoted earlier conversation ("On Mon ... wrote:", "> ...")
  strip_signature() drop signatures, "Sent from my iPhone", legal footers
  is_automated()   auto-replies, bounces, newsletters: never drafted, never counted
  customer_name()  who to greet: their own sign-off, else their display name

Writing:
  format_reply()   greeting line, short paragraphs, sign-off on its own lines, in the
                   customer's language, with the firm's own greeting and sign-off
"""

from __future__ import annotations

import email
import email.policy
import html
import re
from dataclasses import dataclass, field
from email.utils import getaddresses, parseaddr
from html.parser import HTMLParser

# ---- Parsing ---------------------------------------------------------------------


@dataclass
class Mail:
    message_id: str
    thread_ref: str | None          # In-Reply-To
    references: str
    from_name: str
    from_addr: str
    to: list[str]
    subject: str
    date: str
    body: str                       # plain text, quoted history and signature removed
    raw_body: str                   # plain text as received
    attachments: list[str] = field(default_factory=list)
    automated: str | None = None    # why it is automated, or None
    headers: dict = field(default_factory=dict)


class _Text(HTMLParser):
    """HTML to text, keeping line breaks where the layout had them."""

    BREAKS = {"br", "p", "div", "li", "tr", "h1", "h2", "h3", "h4", "blockquote"}

    def __init__(self):
        super().__init__()
        self.out: list[str] = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("style", "script", "head"):
            self.skip += 1
        if tag in self.BREAKS:
            self.out.append("\n")
        if tag == "blockquote":
            self.out.append("\n> ")

    def handle_endtag(self, tag):
        if tag in ("style", "script", "head"):
            self.skip = max(0, self.skip - 1)
        if tag in self.BREAKS:
            self.out.append("\n")

    def handle_data(self, data):
        if not self.skip:
            self.out.append(data)


def html_to_text(s: str) -> str:
    p = _Text()
    p.feed(s)
    text = html.unescape("".join(p.out))
    text = re.sub(r"[ \t\xa0]+", " ", text)
    return re.sub(r"\n\s*\n\s*\n+", "\n\n", text).strip()


def parse(raw: bytes | str) -> Mail:
    msg = email.message_from_bytes(raw if isinstance(raw, bytes) else raw.encode("utf-8"), policy=email.policy.default)
    plain, htmlpart, attachments = None, None, []
    for part in msg.walk():
        if part.is_multipart():
            continue
        disp = part.get_content_disposition()
        fname = part.get_filename()
        if disp == "attachment" or (fname and disp != "inline"):
            attachments.append(fname or "unnamed attachment")
            continue
        ctype = part.get_content_type()
        try:
            content = part.get_content()
        except (LookupError, UnicodeDecodeError):
            content = part.get_payload(decode=True).decode("utf-8", errors="replace")
        if ctype == "text/plain" and plain is None:
            plain = content
        elif ctype == "text/html" and htmlpart is None:
            htmlpart = content
    text = plain if plain and plain.strip() else html_to_text(htmlpart or "")
    text = text.replace("\r\n", "\n")
    from_name, from_addr = parseaddr(str(msg.get("From", "")))
    headers = {k.lower(): str(v) for k, v in msg.items()}
    m = Mail(
        message_id=str(msg.get("Message-ID", "")).strip(),
        thread_ref=(str(msg.get("In-Reply-To")).strip() if msg.get("In-Reply-To") else None),
        references=str(msg.get("References", "")).strip(),
        from_name=from_name.strip().strip('"'),
        from_addr=from_addr.lower(),
        to=[a for _, a in getaddresses([str(msg.get("To", ""))])],
        subject=str(msg.get("Subject", "")).strip(),
        date=str(msg.get("Date", "")),
        body="",
        raw_body=text,
        attachments=attachments,
        headers=headers,
    )
    m.body = strip_signature(strip_quoted(text))
    m.automated = is_automated(headers, from_addr, m.subject, text)
    return m


# ---- Quoted history and signatures ----------------------------------------------

_QUOTE_HEADERS = [
    r"^On .{4,200}(wrote|schreef|a écrit|escribió|schrieb):\s*$",              # Gmail/Apple, EN
    r"^Op .{4,200}schreef .{1,120}:\s*$",                                   # NL
    r"^Am .{4,200}schrieb .{1,120}:\s*$",                                   # DE
    r"^Le .{4,200}a écrit\s*:\s*$",                                         # FR
    r"^El .{4,200}escribió:\s*$",                                           # ES
    r"^-{2,}\s*(Original Message|Oorspronkelijk bericht|Ursprüngliche Nachricht|Forwarded message)\s*-{2,}\s*$",
    r"^_{10,}\s*$",                                                         # Outlook separator line
    r"^(From|Van|Von|De):\s.+$\n^(Sent|Verzonden|Gesendet|Envoyé|Date|Datum):\s",  # Outlook header block
]
_QUOTE_RE = re.compile("|".join(f"(?:{p})" for p in _QUOTE_HEADERS), re.M | re.I)


def strip_quoted(text: str) -> str:
    """Cut the earlier conversation that mail clients paste under a reply."""
    text = text.replace("\r\n", "\n")
    m = _QUOTE_RE.search(text)
    if m:
        text = text[: m.start()]
    # Gmail sometimes wraps the "On ... wrote:" line in two; catch the split form.
    m = re.search(r"^On .{4,120}\n.{0,120}wrote:\s*$", text, re.M)
    if m:
        text = text[: m.start()]
    # Trailing block of "> " lines.
    lines = text.rstrip().split("\n")
    while lines and (lines[-1].startswith(">") or not lines[-1].strip()):
        lines.pop()
    return "\n".join(lines).strip()


_SIG_MARKERS = re.compile(
    r"^(-- ?|—+|Sent from my \w+.*|Sent from (Outlook|Mail|Yahoo Mail).*|Get Outlook for \w+.*|"
    r"Verstuurd vanaf mijn \w+.*|Verzonden (vanaf|met) .*|Von meinem \w+ gesendet.*|Gesendet von .*|"
    r"Envoyé de mon \w+.*|Enviado desde mi \w+.*)\s*$",
    re.M | re.I,
)
_DISCLAIMER = re.compile(
    r"\n[^\n]*(this (e-?mail|message)[^\n]{0,80}(confidential|intended (solely |only )?for)|"
    r"disclaimer|deze e-?mail is uitsluitend bestemd|diese e-?mail enthält vertrauliche)[\s\S]*$",
    re.I,
)


def strip_signature(text: str) -> str:
    m = _SIG_MARKERS.search(text)
    if m:
        text = text[: m.start()]
    text = _DISCLAIMER.sub("", text)
    return text.strip()


# ---- Automated mail ----------------------------------------------------------------

_AUTO_SUBJECT = re.compile(
    r"^(auto(matic)?[ -]?reply|automatisch antwoord|automatische antwort|out of (the )?office|afwezig|"
    r"abwesenheit|delivery status notification|undeliverable|undelivered mail|mail delivery failed|"
    r"returned mail|réponse automatique)",
    re.I,
)
_AUTO_SENDER = re.compile(r"^(mailer-daemon|postmaster|no-?reply|do-?not-?reply|noreply|bounce)", re.I)


def is_automated(headers: dict, from_addr: str, subject: str, body: str = "") -> str | None:
    """Why this mail must not be answered, or None. Answering an auto-reply starts two
    robots replying to each other."""
    h = {k.lower(): v.lower() for k, v in headers.items()}
    if h.get("auto-submitted", "no") not in ("", "no"):
        return "auto-submitted"
    if h.get("precedence", "") in ("bulk", "junk", "list", "auto_reply"):
        return f"precedence {h['precedence']}"
    if any(k in h for k in ("x-autoreply", "x-autorespond", "x-auto-response-suppress")) and _AUTO_SUBJECT.search(subject or ""):
        return "auto-reply"
    if "list-unsubscribe" in h or "list-id" in h:
        return "newsletter or mailing list"
    if _AUTO_SENDER.match((from_addr or "").split("@")[0]):
        return "automated sender"
    if _AUTO_SUBJECT.search(subject or ""):
        return "auto-reply subject"
    return None


# ---- Who to greet ------------------------------------------------------------------

_SIGNOFF_WORDS = (
    r"thanks|thank you|many thanks|thx|regards|kind regards|best regards|best|cheers|all the best|"
    r"best wishes|sincerely|groet(?:jes)?|met vriendelijke groet(?:en)?|mvg|vriendelijke groet|"
    r"viele grüße|liebe grüße|lg|mit freundlichen grüßen|saludos|gracias|merci|cordialement|bedankt|dank je|danke"
)
_SIGNOFF_LINE = re.compile(rf"^\s*(?:{_SIGNOFF_WORDS})\b[\s,!.]*(?P<name>.*)$", re.I)
_NAME_TOKEN = re.compile(r"^[A-ZÀ-ÖØ-Þ][a-zß-öø-ÿ'\-]{1,20}$")
_NOT_NAMES = {
    "Support", "Team", "Info", "Admin", "Office", "Student", "Customer", "Service", "Hello", "Contact",
    "Thanks", "Regards", "Best", "Sent", "Mail", "No", "Reply", "Noreply", "The", "Me", "Us", "Everyone",
    "Mr", "Mrs", "Ms", "Dr", "Sir", "Madam",
}
_GENERIC_LOCALPARTS = {"info", "admin", "contact", "hello", "mail", "support", "office", "student", "test", "sales", "team"}


def _first_name(candidate: str) -> str | None:
    words = [w.strip(",.!") for w in candidate.strip().split()]
    if not 1 <= len(words) <= 3:
        return None
    first = words[0]
    if _NAME_TOKEN.match(first) and first not in _NOT_NAMES and all(w[:1].isupper() for w in words if w):
        return first
    return None


def name_from_signoff(body: str) -> str | None:
    """'Thanks,\\nPriya' or 'Kind regards, Priya Nair' or a bare name on the last line."""
    lines = [l.strip() for l in body.strip().split("\n") if l.strip()]
    tail = lines[-4:]
    for i, line in enumerate(tail):
        m = _SIGNOFF_LINE.match(line)
        if not m:
            continue
        same = _first_name(m.group("name")) if m.group("name") else None
        if same:
            return same
        if i + 1 < len(tail):
            nxt = _first_name(tail[i + 1])
            if nxt:
                return nxt
    if len(lines) >= 2:
        return _first_name(lines[-1]) if len(lines[-1]) <= 40 and not lines[-1].endswith(("?", ".")) else None
    return None


def customer_name(body: str, from_name: str = "", from_addr: str = "") -> str | None:
    """How the customer signs off, else their display name, else a name-shaped address
    (firstname.lastname@). None if unsure: 'Hi,' is better than a wrong name."""
    n = name_from_signoff(body)
    if n:
        return n
    if from_name:
        disp = from_name.split(",")[-1] if "," in from_name else from_name   # "Nair, Priya"
        n = _first_name(disp.strip())
        if n:
            return n
    local = (from_addr or "").split("@")[0]
    parts = re.split(r"[._\-]", local)
    if len(parts) >= 2 and parts[0].isalpha() and len(parts[0]) >= 2 and parts[0].lower() not in _GENERIC_LOCALPARTS:
        return parts[0].capitalize()
    return None


# ---- Writing -------------------------------------------------------------------------

_NL = set("de het een en ik je jij u mijn niet wat hoe waar wanneer kan kunnen is zijn van voor met op dat die graag hoi bedankt".split())
_DE = set("der die das und ich du sie mein nicht was wie wo wann kann können ist sind von für mit auf dass hallo danke bitte".split())


_EN = set("the a an and or to of in on for is are was were be it this that you your we my me not no can "
           "could would will with at from have has had do does did but so if please thanks what when where why "
           "how just still get got i im i'm it's don't back".split())


def language(text: str) -> str:
    """English unless there is clear evidence otherwise: at least two Dutch or German
    words, and more of them than English ones. One shared word ('u' is Dutch for 'you'
    and chat English for 'you') is not evidence."""
    words = re.findall(r"[a-zà-ÿß']+", (text or "").lower())
    en = sum(w in _EN for w in words)
    nl = len({w for w in words if w in _NL})
    de = len({w for w in words if w in _DE})
    best, lang = max((nl, "nl"), (de, "de"))
    return lang if best >= 2 and best > en else "en"


_GREETING = re.compile(
    r"^\s*(hi|hello|hey|dear|hiya|good (morning|afternoon|evening)|hoi|hallo|beste|dag|liebe|lieber|"
    r"guten tag|hola|bonjour)\b[^,!.\n]{0,40}[,!.]?\s*",
    re.I,
)
# A sign-off is the word followed by a comma, "!" or a line break, then at most a name:
# "Kind regards, the Harbourbrook team". "Thanks for your patience." is not one.
_TRAILING_SIGNOFF = re.compile(
    rf"\s*(?<![\w])(?:{_SIGNOFF_WORDS}|greetings)(?:[,!]|\s*\n)\s*(?:(?:the |het |das )?[\w\-]+(?: [\w\-]+){{0,3}})?[.!]?\s*$",
    re.I,
)


def strip_frame(text: str) -> str:
    """The body of a reply without its greeting line and sign-off."""
    text = _GREETING.sub("", text.strip(), count=1)
    return _TRAILING_SIGNOFF.sub("", text).strip()


def split_paragraphs(body: str, max_sentences: int = 3) -> str:
    """Keep the writer's paragraphs; break very long single paragraphs into short ones."""
    paras = [p.strip() for p in re.split(r"\n\s*\n", body) if p.strip()]
    out = []
    for p in paras:
        sents = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\[\"'(])", p.replace("\n", " "))
        if len(sents) <= max_sentences:
            out.append(" ".join(sents))
            continue
        for i in range(0, len(sents), max_sentences - 1 if max_sentences > 2 else 2):
            out.append(" ".join(sents[i : i + (max_sentences - 1 if max_sentences > 2 else 2)]))
    return "\n\n".join(out)


def format_reply(body: str, style: dict | None, name: str | None, lang: str = "en") -> str:
    """Greeting, short paragraphs, sign-off on its own lines. `style` comes from the
    firm config (email_style); without one, the text is returned unchanged (tweets)."""
    if not style:
        return body.strip()
    text = body.strip()
    text = _GREETING.sub("", text, count=1)
    text = _TRAILING_SIGNOFF.sub("", text)
    text = re.sub(r"\[\[name\]\],?\s*", "", text) if not name else text.replace("[[name]]", name)
    text = text.strip()
    if text:
        text = text[0].upper() + text[1:]
    s = style.get(lang) or style.get("en") or {}
    greet = (s.get("greeting_named", "Hi {name},").format(name=name) if name else s.get("greeting", "Hi,"))
    signoff = s.get("signoff", "Kind regards,\nThe support team")
    return f"{greet}\n\n{split_paragraphs(text)}\n\n{signoff}"
