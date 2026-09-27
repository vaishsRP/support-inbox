from datetime import datetime, timezone

import pytest

from inbox import actions
from inbox.rules import check_outgoing, load_rules
from inbox.store import connect

RULES = load_rules()
MON = datetime(2017, 11, 6, 9, 0, tzinfo=timezone.utc)


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "t.db")
    yield c
    c.close()


def test_tracked_commitment_becomes_a_row_once(conn):
    reply = "Sorry about the bag. Someone will call you Thursday with an update."
    hits = check_outgoing(reply, RULES, sent_at=MON)
    ids = actions.record_commitments(conn, hits, customer_id="c1", thread_id="t1", origin="sent", sent_at=MON)
    again = actions.record_commitments(conn, hits, customer_id="c1", thread_id="t1", origin="sent", sent_at=MON)
    assert len(ids) == 1 and again == []
    [row] = actions.open_rows(conn)
    assert row["kind"] == "commitment" and row["due_at"].startswith("2017-11-09")


def test_checks_in_a_draft_become_rows(conn):
    draft = "We're looking into it. [[CHECK: payment status for order 4471 in billing]] Thanks!"
    actions.record_checks(conn, draft, customer_id="c1", thread_id="t1")
    [row] = actions.open_rows(conn)
    assert row["kind"] == "check" and row["what"] == "payment status for order 4471 in billing"


def test_digest_sections(conn):
    hits = check_outgoing("We'll call you tomorrow. We'll be in touch shortly.", RULES, sent_at=MON)
    actions.record_commitments(conn, hits, customer_id="c1", thread_id="t1", origin="sent", sent_at=MON)
    actions.add(conn, "spike", "31 customers about login failures since 9am")
    wednesday = datetime(2017, 11, 8, 8, 0, tzinfo=timezone.utc)
    text = actions.digest(conn, now=wednesday)
    assert "OVERDUE promises (1)" in text
    assert "no clear deadline (1)" in text
    assert "Spikes still open (1)" in text


def test_done_rows_leave_the_digest(conn):
    rid = actions.add(conn, "check", "look up refund status")
    actions.mark_done(conn, rid)
    assert "Nothing open" in actions.digest(conn, now=MON)


def test_leaked_placeholder_is_reported(conn):
    conn.execute(
        "INSERT INTO drafts (thread_id, customer_msg_id, customer_id, created_at, route, sent_text, sent_at) "
        "VALUES ('t9','m9','c9',?, 'reuse', 'Hi [[name]], your bag is on its way.', ?)",
        (MON.isoformat(), MON.isoformat()),
    )
    assert "Placeholders that reached a customer" in actions.digest(conn, now=MON)
