"""The tool cannot send mail. This test is the enforcement.

Gmail permissions cannot enforce it: any scope that can create drafts can also send.
So the build fails if code that sends mail appears anywhere outside this test file.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN = [
    r"\bsmtplib\b",
    r"\bsendmail\b",
    r"\bsend_message\b",
    r"messages\(\)\s*\.\s*send\b",
    r"drafts\(\)\s*\.\s*send\b",
    r"\bmessages\.send\b",
    r"\bdrafts\.send\b",
    r"gmail\.send\b",                     # the send-only OAuth scope
    r"\byagmail\b|\bredmail\b|\bflask_mail\b|\bsendgrid\b|\bmailgun\b|\bresend\b|\bbrevo\b",
]
SCANNED = [ROOT / "src", ROOT / "scripts", ROOT / "Dockerfile", ROOT / "config"]


def _files():
    for base in SCANNED:
        if base.is_file():
            yield base
        elif base.is_dir():
            yield from (p for p in base.rglob("*") if p.is_file() and p.suffix in {".py", ".yaml", ".yml", ".toml", ""})


def test_no_code_that_sends_mail():
    offenders = []
    for path in _files():
        text = path.read_text(encoding="utf-8", errors="ignore")
        for pat in FORBIDDEN:
            for m in re.finditer(pat, text, re.I):
                line = text.count("\n", 0, m.start()) + 1
                offenders.append(f"{path.relative_to(ROOT)}:{line}: {m.group(0)}")
    assert not offenders, "Sending code is not allowed anywhere:\n" + "\n".join(offenders)


def test_the_scan_actually_finds_things(tmp_path):
    bad = "service.users().messages().send(userId='me', body=msg).execute()"
    assert any(re.search(p, bad, re.I) for p in FORBIDDEN)
