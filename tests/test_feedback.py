from datetime import datetime, timezone

import pytest

from inbox.feedback import draft_body, edit_similarity, measures, outcome_for, record_sent
from inbox.rules import load_rules
from inbox.store import connect

RULES = load_rules()
T = datetime(2017, 11, 6, 15, 0, tzinfo=timezone.utc)
DRAFT = (
    "[[REVIEW: delete this line once you have read the draft]]\n"
    "Sorry your bag is late. Please file a report with our Baggage team.\n"
    "[[AGENT NOTE: closest past answer is from 2017-10-02]]"
)


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "f.db")
    with c:
        c.execute(
            "INSERT INTO messages VALUES ('m1','t1',NULL,'c1',1,'2017-11-06T12:00:00+00:00','@TestAir where is my bag')"
        )
        c.execute(
            "INSERT INTO drafts (thread_id, customer_msg_id, customer_id, created_at, route, confidence, draft_text) "
            "VALUES ('t1','m1','c1','2017-11-06T12:00:00+00:00','reuse','high',?)",
            (DRAFT,),
        )
    yield c
    c.close()


def test_draft_body_drops_review_line_and_notes():
    assert draft_body(DRAFT) == "Sorry your bag is late. Please file a report with our Baggage team."


def test_untouched_draft_scores_one():
    assert edit_similarity(DRAFT, "Sorry your bag is late. Please file a report with our Baggage team.") == 1.0


@pytest.mark.parametrize(
    "sim, expected", [(1.0, "sent_as_is"), (0.7, "edited"), (0.1, "discarded")]
)
def test_outcomes(sim, expected):
    assert outcome_for(DRAFT, sim) == expected


def test_record_sent_diffs_tracks_and_feeds_the_pool(conn):
    sent = "Sorry your bag is late, Ann. Please file a report with our Baggage team. Someone will call you Thursday."
    out = record_sent(conn, 1, sent, T, RULES)
    assert out["outcome"] == "edited" and 0.5 < out["edit_similarity"] < 1
    # The promise the agent typed in by hand is on the action list.
    assert conn.execute("SELECT what FROM actions").fetchone()[0].startswith("Someone will call you Thursday")
    # The sent reply is now an approved answer, with the customer's name redacted.
    pair = conn.execute("SELECT answer, source FROM pairs").fetchone()
    assert pair["source"] == "sent" and "Ann" not in pair["answer"]


def test_measures_report_the_three_numbers(conn):
    record_sent(conn, 1, "Sorry your bag is late. Please file a report with our Baggage team.", T, RULES)
    m = measures(conn)
    assert m["mails"] == 1 and m["drafted"] == 1 and m["draft_rate"] == 1.0
    assert m["edit_similarity_median"] == 1.0
    assert m["outcomes"] == {"sent_as_is": 1}
