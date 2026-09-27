import json
from datetime import datetime, timezone

from fake_gmail import fake_service
from inbox.draft import Drafter, Incoming, facts
from inbox.gmail import Mailbox
from inbox.history import import_mailbox
from inbox.llm import StubModel
from inbox.rules import load_rules
from inbox.store import connect

from conftest import at

RULES = load_rules()
QUIET = lambda *a, **k: None  # noqa: E731


def js(**kw):
    return json.dumps(kw)


def test_importing_a_mailbox_pairs_questions_with_the_replies_sent(tmp_path):
    from conftest import make_firm

    firm = make_firm(tmp_path)   # an empty mailbox database, no sample pairs
    g = fake_service()
    first = g.deliver(frm="Priya Nair <priya@example.com>", subject="Deposit",
                      body="When do I get my deposit back?\n\nThanks,\nPriya")
    t = g.msgs[first]["threadId"]
    g.team_replied(t, "Hi Priya,\n\nWithin 30 days of move-out.\n\nKind regards,\nThe team\n\n"
                      "On Mon, Priya <priya@example.com> wrote:\n> When do I get my deposit back?")
    g.deliver(frm="Priya Nair <priya@example.com>", subject="Re: Deposit", body="It has been 35 days now.", thread=t)
    g.team_replied(t, "Sorry Priya, we have asked finance to check today.")
    g.deliver(frm="Out of office <oof@example.com>", subject="Automatic reply: away", body="Away",
              headers={"Auto-Submitted": "auto-replied"}, thread=t)
    conn = connect(firm.db_path)
    rep = import_mailbox(Mailbox(g, g.me), conn, firm, months=12, log=QUIET)
    assert rep.pairs == 2 and rep.skipped_automated == 1
    rows = conn.execute("SELECT question, answer, is_followup, customer_id FROM pairs WHERE source='mailbox' "
                        "ORDER BY answered_at").fetchall()
    assert rows[0]["answer"] == "Within 30 days of move-out."          # no greeting, sign-off or quoted text
    assert rows[1]["is_followup"] == 1 and rows[1]["customer_id"] == "priya@example.com"
    again = import_mailbox(Mailbox(g, g.me), conn, firm, months=12, log=QUIET)
    assert again.pairs == 0                                             # re-running adds nothing


def _pair(conn, i, q, a, when, customer="c9", thread=None):
    conn.execute(
        "INSERT INTO pairs (thread_id, customer_msg_id, customer_id, asked_at, answered_at, question, answer, answer_raw, "
        "is_followup, is_deflection, source) VALUES (?,?,?,?,?,?,?,?,0,0,'mailbox')",
        (thread or f"h{i}", f"hm{i}", customer, when, when, q, a, a),
    )


def test_the_newest_answer_wins_and_a_policy_change_is_flagged(firm):
    conn = connect(firm.db_path)
    with conn:
        _pair(conn, 1, "when is my deposit refunded after moving out", "Deposits are paid back within 30 days.", "2017-10-02T10:00:00+00:00")
        _pair(conn, 2, "when is my deposit refunded after moving out", "Deposits are paid back within 21 days.", "2017-10-08T10:00:00+00:00")
    conn.close()
    seen = []

    def reply(system, user):
        seen.append(user)
        return js(fits=True, reply="Deposits are paid back within 21 days.", uncovered=[])

    conn = connect(firm.db_path)
    d = Drafter(firm, conn, StubModel(reply), RULES)
    res = d.handle(Incoming("n1", "tn1", "c1", "when is my deposit refunded after moving out", at(20)))
    assert "within 21 days" in seen[0] and "within 30 days" not in seen[0].split("APPROVED REPLY")[1]
    assert "earlier replies said 30 days (2017-10-02); the newest says 21 days (2017-10-08)" in res.draft


def test_the_customers_earlier_conversations_are_given_dated(firm):
    conn = connect(firm.db_path)
    with conn:
        _pair(conn, 3, "where is my deposit", "Within 30 days of move-out.", "2017-10-03T10:00:00+00:00",
              customer="lina@example.com", thread="old-thread")
    conn.close()
    seen = []

    def reply(system, user):
        seen.append(user)
        return js(fits=True, reply="Please file a report with our Baggage team.", uncovered=[])

    conn = connect(firm.db_path)
    d = Drafter(firm, conn, StubModel(reply), RULES)
    d.handle(Incoming("n2", "new-thread", "lina@example.com", "My bag did not arrive in Chicago, where is my bag?", at(20)))
    assert "CUSTOMER'S EARLIER CONVERSATIONS" in seen[0]
    assert '2017-10-03: they wrote "where is my deposit"' in seen[0]


def test_facts_in_english_and_dutch():
    assert facts("within 30 days, EUR 85 cleaning, 5 working days") == {"30 days", "eur 85", "5 working days"}
    assert facts("binnen 21 dagen, € 60") == {"21 dagen", "€ 60"}
