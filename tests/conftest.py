import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

# Tests never download the embedding model and never call a real language model.
os.environ["INBOX_FAKE_EMBED"] = "1"

from inbox.config import Firm  # noqa: E402
from inbox.store import connect  # noqa: E402

PAIRS = [
    # (question, answer, asked_at, customer)
    ("My bag did not arrive in Chicago, where is my bag?",
     "Sorry your bag didn't make it, [[name]]. Please file a report with our Baggage team at the airport so we can track it down.",
     "2017-10-02T10:00:00+00:00", "c1"),
    ("Can I bring a stroller on board?",
     "Strollers can be checked at the gate at no charge. Here are our guidelines: [link]",
     "2017-10-03T10:00:00+00:00", "c2"),
    ("wifi not working on my flight",
     "Sorry about the Wi-Fi. Please ask a flight attendant to reset the system.",
     "2017-10-04T10:00:00+00:00", "c3"),
]


def make_firm(tmp_path: Path, **thresholds) -> Firm:
    raw = {"thresholds": {"reuse": 0.6, "docs": 0.5, "high": 0.9, "medium": 0.75, **thresholds}}
    return Firm(
        key="testfirm",
        name="Test Air",
        timezone="UTC",
        corpus_kind="twcs",
        corpus_path=tmp_path / "none.csv",
        brand_handle="TestAir",
        data_dir=tmp_path,
        public_numbers=[],
        raw=raw,
    )


@pytest.fixture
def firm(tmp_path):
    f = make_firm(tmp_path)
    conn = connect(f.db_path)
    with conn:
        for i, (q, a, t, c) in enumerate(PAIRS):
            conn.execute(
                "INSERT INTO pairs (thread_id, customer_msg_id, customer_id, asked_at, answered_at, question, "
                "answer, answer_raw, is_followup, is_deflection) VALUES (?,?,?,?,?,?,?,?,0,0)",
                (f"t{i}", f"m{i}", c, t, t, q, a, a),
            )
    conn.close()
    return f


def at(day: int) -> datetime:
    return datetime(2017, 10, day, 12, 0, tzinfo=timezone.utc)
