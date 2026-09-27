import json

import pytest

from inbox.docs import add_doc
from inbox.draft import REVIEW_LINE, Drafter, Incoming
from inbox.llm import ModelUnavailable, StubModel
from inbox.rules import load_rules
from inbox.store import connect

from conftest import at

RULES = load_rules()


def js(**kw) -> str:
    return json.dumps(kw)


def drafter(firm, reply):
    conn = connect(firm.db_path)
    return Drafter(firm, conn, StubModel(reply), RULES), conn


def mail(text, day=10, thread="new1", customer="c9", thread_msgs=()):
    return Incoming("mX", thread, customer, text, at(day), list(thread_msgs))


def test_close_question_reuses_the_approved_answer(firm):
    reply = js(fits=True, reply="Sorry your bag didn't make it. Please file a report with our Baggage team.", uncovered=[])
    d, conn = drafter(firm, reply)
    res = d.handle(mail("My bag did not arrive in Chicago, where is it?"))
    assert res.route == "reuse"
    assert res.draft.startswith(REVIEW_LINE)
    assert len(res.labels) == 1 and res.labels[0].startswith("AI/draft-ready/")   # one label per email
    # The adapted draft was built from the redacted answer: the prompt carried [[name]], not a name.
    assert "[[name]]" in d.model.calls[0][1]
    assert conn.execute("SELECT route FROM drafts").fetchone()[0] == "reuse"


def test_nothing_close_means_no_reply_text_and_a_reason(firm):
    d, _ = drafter(firm, js(questions=["Which airport are you at?"], issue="lost item"))
    res = d.handle(mail("Someone took my knitting needles at security in Oslo"))
    assert res.route == "refused"
    assert res.draft is None
    assert "nothing close enough" in res.reason
    assert any("worth asking the customer" in n for n in res.notes)
    assert res.labels == ["AI/no-answer"]


def test_legal_threat_is_blocked_before_any_model_call(firm):
    d, _ = drafter(firm, "should never be called")
    res = d.handle(mail("My bag did not arrive, my lawyer will hear about this"))
    assert res.route == "blocked" and res.draft is None
    assert "legal" in res.reason
    assert d.model.calls == []


def test_commitment_in_draft_becomes_placeholder(firm):
    reply = js(fits=True, reply="Sorry about your bag. We will refund your bag fee today.", uncovered=[])
    d, _ = drafter(firm, reply)
    res = d.handle(mail("My bag did not arrive in Chicago, where is my bag?"))
    assert "We will refund" not in res.draft
    assert "[[NEEDS APPROVAL: refund, ask billing lead]]" in res.draft
    assert "AI/needs-approval" in res.labels


def test_answer_that_does_not_fit_falls_back_to_refusal(firm):
    def reply(system, user):
        if "adapt" in system:
            return js(fits=False, reply="", uncovered=[])
        return js(questions=[], issue="unclear")

    d, _ = drafter(firm, reply)
    res = d.handle(mail("My bag did not arrive in Chicago, where is my bag?"))
    assert res.route == "refused"


def test_uncovered_part_caps_confidence(firm):
    reply = js(fits=True, reply="Please file a report with our Baggage team.", uncovered=["seat upgrade question"])
    d, _ = drafter(firm, reply)
    res = d.handle(mail("My bag did not arrive in Chicago, where is my bag? Also can I upgrade?"))
    assert res.confidence in ("medium", "low")
    assert "not answered yet: seat upgrade question" in res.draft


def test_check_placeholder_goes_on_the_action_list(firm):
    reply = js(fits=True, reply="Your bag is [[CHECK: bag trace status for this customer]]. Sorry!", uncovered=[])
    d, conn = drafter(firm, reply)
    d.handle(mail("My bag did not arrive in Chicago, where is my bag?"))
    row = conn.execute("SELECT kind, what FROM actions").fetchone()
    assert tuple(row) == ("check", "bag trace status for this customer")


def test_model_down_is_a_stated_failure_not_a_drop(firm):
    def down(system, user):
        raise ModelUnavailable("primary: rate limited; local: unreachable")

    d, conn = drafter(firm, down)
    res = d.handle(mail("My bag did not arrive in Chicago, where is my bag?"))
    assert res.route == "failed" and "model unavailable" in res.reason
    assert conn.execute("SELECT COUNT(*) FROM drafts").fetchone()[0] == 1


def test_no_future_answers_in_replay(firm):
    # Asked on 1 Oct: every pair in the pool is from later, so nothing may be reused.
    d, _ = drafter(firm, js(questions=[], issue=""))
    res = d.handle(mail("My bag did not arrive in Chicago, where is my bag?", day=1))
    assert res.route == "refused"


def test_follow_up_never_reproposes_the_answer_already_sent(firm):
    sent = "Sorry your bag didn't make it, Ann. Please file a report with our Baggage team at the airport so we can track it down."
    d, _ = drafter(firm, js(questions=[], issue=""))
    res = d.handle(
        mail(
            "My bag did not arrive in Chicago, where is my bag? still nothing",
            thread_msgs=[("customer", "bag missing"), ("firm", sent)],
        )
    )
    assert all(s.get("pair_id") != 1 for s in res.sources)


def test_dated_note_covers_a_new_incident(firm):
    conn = connect(firm.db_path)
    with conn:
        add_doc(conn, "note", "App login broken after v2",
                "Since the v2 app release, login fails with error 403. Workaround: update the app to 2.0.1.",
                created_at=at(9), expires_in_days=7)
    conn.close()
    reply = js(fits=True, reply="Please update the app to 2.0.1. [source: App login broken after v2]", uncovered=[])
    d, _ = drafter(firm, reply)
    res = d.handle(mail("app login fails error 403 since v2 release"))
    assert res.route == "docs"
    # Two weeks later the note has expired and the same mail is refused.
    d2, _ = drafter(firm, js(questions=[], issue=""))
    assert d2.handle(mail("app login fails error 403 since v2 release", day=25)).route == "refused"


def test_old_answer_about_a_situation_is_flagged_as_possibly_stale(firm):
    conn = connect(firm.db_path)
    with conn:
        conn.execute("UPDATE pairs SET answer = ? WHERE id = 3",
                     ("We're aware of a Wi-Fi outage right now and expect it back by tonight.",))
    conn.close()
    d, _ = drafter(firm, js(fits=True, reply="We're aware of a Wi-Fi outage and expect it back by tonight.", uncovered=[]))
    res = d.handle(mail("wifi not working on my flight", day=20))
    assert "check it is still true" in res.draft
    assert res.confidence != "high"


def test_live_note_wins_over_an_old_answer_that_only_looks_similar(firm):
    conn = connect(firm.db_path)
    with conn:
        add_doc(conn, "note", "wifi not working on flights this week",
                "wifi not working on my flight: the onboard system is down fleet-wide until Friday, no reset helps.",
                created_at=at(9), expires_in_days=7)
    conn.close()

    def reply(system, user):
        if "SOURCES" in user:
            return js(fits=True, reply="The onboard Wi-Fi is down until Friday. [source: wifi not working on flights this week]", uncovered=[])
        return js(fits=True, reply="Please ask a flight attendant to reset the system.", uncovered=[])

    d, _ = drafter(firm, reply)
    res = d.handle(mail("wifi not working on my flight"))
    assert res.route == "docs"
    assert "reset" not in res.draft


def test_non_english_draft_is_checked_through_translation(firm):
    def reply(system, user):
        if "adapt" in system:
            return js(fits=True, reply="Lo sentimos mucho por su maleta. Le reembolsaremos la tarifa hoy mismo.", uncovered=[])
        if "Translate" in system:
            return js(english=["We are very sorry about your bag.", "We will refund the fee today."])
        return js(questions=[], issue="")

    d, _ = drafter(firm, reply)
    res = d.handle(mail("My bag did not arrive in Chicago, where is my bag?"))
    assert "reembolsaremos" not in res.draft
    assert "[[NEEDS APPROVAL: refund" in res.draft


def test_draft_has_no_reference_lines_only_the_review_line(firm):
    d, _ = drafter(firm, js(fits=True, reply="Please file a report with our Baggage team.", uncovered=[]))
    res = d.handle(mail("My bag did not arrive in Chicago, where is my bag?"))
    assert "closest past answer" not in res.draft
    assert res.draft.count("[[") == 1          # just the review line
    assert any("closest past answer" in n for n in res.notes)   # still on the dashboard


STYLE = {"en": {"greeting_named": "Hi {name},", "greeting": "Hi,", "signoff": "Kind regards,\nThe Test Air team"}}


def test_email_style_firm_gets_greeting_name_and_signoff(firm):
    firm.raw["email_style"] = STYLE
    d, _ = drafter(firm, js(fits=True, reply="Hi [[name]], please file a report with our Baggage team. Kind regards, the team", uncovered=[]))
    m = mail("My bag did not arrive in Chicago, where is my bag?\n\nThanks,\nPriya")
    res = d.handle(m)
    body = res.draft.split("\n\n", 1)[1]
    assert body == "Hi Priya,\n\nPlease file a report with our Baggage team.\n\nKind regards,\nThe Test Air team"


def test_docs_drafts_get_the_teams_tone_and_no_citations(firm):
    conn = connect(firm.db_path)
    with conn:
        add_doc(conn, "standing", "Baggage policy", "Lost bags: file a report at the baggage desk within 24 hours.")
    conn.close()
    reply = js(fits=True, reply="Please file a report at the baggage desk within 24 hours.", uncovered=[], used=["Baggage policy"])
    d, _ = drafter(firm, reply)
    res = d.handle(mail("lost bags report baggage desk 24 hours"))
    if res.route == "docs":
        system, user = d.model.calls[-1] if "SOURCES" in d.model.calls[-1][1] else d.model.calls[0]
        assert "TEAM'S RECENT REPLIES" in user
        assert "[source:" not in res.draft


def test_attachment_is_flagged_in_the_draft(firm):
    d, _ = drafter(firm, js(fits=True, reply="Please file a report with our Baggage team.", uncovered=[]))
    m = mail("My bag did not arrive in Chicago, where is my bag?")
    m.attachments = ["tag.jpg"]
    assert "the customer attached tag.jpg; it was not read" in d.handle(m).draft
