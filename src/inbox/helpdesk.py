"""Importer for a helpdesk-style export: one row per customer message and the reply
the team sent. This is the shape most helpdesks export (Zendesk, Freshdesk, a
spreadsheet), and the shape a firm would onboard with.

Required fields: thread_id, customer_id, asked_at (ISO 8601), question, answer.
Optional: answered_at, is_followup. Accepts .csv, or .yaml with a `pairs:` list.
"""

from __future__ import annotations

import csv
from pathlib import Path

import yaml

from .config import Firm
from .store import connect
from .textclean import clean, is_deflection, redact

REQUIRED = ("thread_id", "customer_id", "asked_at", "question", "answer")


def read_rows(path: Path) -> list[dict]:
    if path.suffix in (".yaml", ".yml"):
        rows = yaml.safe_load(path.read_text(encoding="utf-8"))["pairs"]
    else:
        with path.open(encoding="utf-8", newline="") as f:
            rows = list(csv.DictReader(f))
    for i, r in enumerate(rows):
        missing = [k for k in REQUIRED if not str(r.get(k, "")).strip()]
        if missing:
            raise ValueError(f"{path.name} row {i + 1}: missing {', '.join(missing)}")
    return rows


def import_helpdesk(firm: Firm, log=print) -> dict:
    rows = read_rows(firm.corpus_path)
    conn = connect(firm.db_path)
    with conn:
        conn.execute("DELETE FROM pairs WHERE source = 'corpus'")
        conn.execute("DELETE FROM messages")
        for i, r in enumerate(rows):
            msg_id = str(r.get("msg_id") or f"q{i}")
            asked = str(r["asked_at"])
            answered = str(r.get("answered_at") or asked)
            conn.execute(
                "INSERT INTO messages VALUES (?,?,?,?,1,?,?)",
                (msg_id, str(r["thread_id"]), None, str(r["customer_id"]), asked, str(r["question"])),
            )
            conn.execute(
                "INSERT INTO messages VALUES (?,?,?,?,0,?,?)",
                (f"a{i}", str(r["thread_id"]), msg_id, firm.brand_handle, answered, str(r["answer"])),
            )
            answer = redact(clean(str(r["answer"]), keep_urls=True), firm.public_numbers)
            conn.execute(
                """INSERT INTO pairs (thread_id, customer_msg_id, customer_id, asked_at, answered_at, question,
                   answer, answer_raw, is_followup, is_deflection, source) VALUES (?,?,?,?,?,?,?,?,?,?, 'corpus')""",
                (
                    str(r["thread_id"]), msg_id, str(r["customer_id"]), asked, answered, clean(str(r["question"])),
                    answer, str(r["answer"]), int(str(r.get("is_followup", "0")) in ("1", "true", "True")),
                    int(is_deflection(answer)),
                ),
            )
    n = conn.execute("SELECT COUNT(*) FROM pairs").fetchone()[0]
    docs_loaded = load_docs_dir(firm, conn)
    conn.close()
    log(f"[import] {n} pairs from {firm.corpus_path.name}, {docs_loaded} standing documents")
    return {"pairs": n, "docs": docs_loaded}


def load_docs_dir(firm: Firm, conn) -> int:
    """Standing documents from the firm's docs folder (markdown, first line is the title).
    Replaces standing documents of the same title, so re-importing is safe."""
    from .config import ROOT
    from .docs import add_doc

    folder = firm.raw.get("docs_dir")
    if not folder:
        return 0
    n = 0
    with conn:
        for path in sorted((ROOT / folder).glob("*.md")):
            text = path.read_text(encoding="utf-8").strip()
            title, _, body = text.partition("\n")
            title = title.lstrip("# ").strip()
            conn.execute("DELETE FROM docs WHERE kind = 'standing' AND title = ?", (title,))
            add_doc(conn, "standing", title, body.strip())
            n += 1
    return n
