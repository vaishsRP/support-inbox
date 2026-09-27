import json
from email.message import EmailMessage

import pytest

from fake_gmail import fake_service
from inbox.draft import Drafter
from inbox.gmail import Mailbox, poll_once
from inbox.llm import StubModel
from inbox.rules import load_rules
from inbox.store import connect

RULES = load_rules()
STYLE = {"en": {"greeting_named": "Hi {name},", "greeting": "Hi,", "signoff": "Kind regards,\nThe Test Air team"}}
QUIET = lambda *a, **k: None  # noqa: E731


def reply(system, user):
    if "adapt" in system:
        return json.dumps({"fits": True, "reply": "Please file a report with our Baggage team at the airport.", "uncovered": []})
    return json.dumps({"questions": ["Which airport?"], "issue": "unclear"})


@pytest.fixture
def box(firm):
    firm.raw["email_style"] = STYLE
    g = fake_service()
    mb = Mailbox(g, g.me)
    mb.ensure_labels()
    conn = connect(firm.db_path)
    d = Drafter(firm, conn, StubModel(reply), RULES)
    poll_once(mb, d, conn, firm, RULES, log=QUIET)   # first run: sets the starting point
    yield g, mb, d, conn, firm
    conn.close()


def poll(box):
    g, mb, d, conn, firm = box
    return poll_once(mb, d, conn, firm, RULES, log=QUIET)


def test_first_run_does_not_draft_old_mail(firm):
    firm.raw["email_style"] = STYLE
    g = fake_service()
    g.deliver(frm="Old <old@example.com>", subject="old", body="My bag did not arrive in Chicago, where is my bag?")
    mb = Mailbox(g, g.me)
    mb.ensure_labels()
    conn = connect(firm.db_path)
    r = poll_once(mb, Drafter(firm, conn, StubModel(reply), RULES), conn, firm, RULES, log=QUIET)
    assert r.seen == 0 and g.draftbox == {}


def test_new_mail_gets_a_threaded_draft_with_labels(box):
    g = box[0]
    mid = g.deliver(frm="Priya Nair <priya.nair@example.com>", subject="My bag",
                    body="My bag did not arrive in Chicago, where is my bag?\n\nThanks,\nPriya")
    r = poll(box)
    assert r.drafted == 1
    [draft] = g.draftbox.values()
    assert draft["message"]["threadId"] == g.msgs[mid]["threadId"]
    body = g.draft_body(draft["id"])
    assert "Hi Priya," in body and body.rstrip().endswith("The Test Air team")
    assert "In-Reply-To" in __import__("base64").urlsafe_b64decode(draft["message"]["raw"]).decode()
    names = {v: k for k, v in g.labelmap.items()}
    assert "AI/draft-ready" in [names.get(l) for l in g.msgs[mid]["labelIds"]]


def test_auto_reply_is_labelled_and_never_drafted(box):
    g = box[0]
    mid = g.deliver(frm="priya@example.com", subject="Automatic reply: away", body="I'm away until Monday.",
                    headers={"Auto-Submitted": "auto-replied"})
    r = poll(box)
    assert r.skipped == 1 and g.draftbox == {}
    names = {v: k for k, v in g.labelmap.items()}
    assert "AI/automated" in [names.get(l) for l in g.msgs[mid]["labelIds"]]


def test_legal_threat_gets_a_label_but_no_draft(box):
    g = box[0]
    g.deliver(frm="a@example.com", subject="bag", body="My bag did not arrive. My lawyer will contact you.")
    r = poll(box)
    assert r.blocked == 1 and g.draftbox == {}


def test_what_the_person_sends_is_diffed_tracked_and_learned(box):
    g, mb, d, conn, firm = box
    g.deliver(frm="Priya <priya@example.com>", subject="My bag", body="My bag did not arrive in Chicago, where is my bag?")
    poll(box)
    [did] = list(g.draftbox)
    g.human_sends(did, "Hi Priya,\n\nPlease file a report with our Baggage team. Someone will call you tomorrow.\n\n"
                       "Kind regards,\nThe Test Air team")
    r = poll(box)
    assert r.sent_captured == 1
    row = conn.execute("SELECT outcome, edit_similarity FROM drafts ORDER BY id DESC LIMIT 1").fetchone()
    assert row["outcome"] == "edited" and 0.3 < row["edit_similarity"] < 1
    assert conn.execute("SELECT kind FROM actions").fetchone()[0] == "commitment"
    learned = conn.execute("SELECT answer FROM pairs WHERE source = 'sent'").fetchone()[0]
    assert "Someone will call you tomorrow" in learned and "Hi Priya" not in learned


def test_second_mail_replaces_an_untouched_draft_but_not_an_edited_one(box):
    g = box[0]
    first = g.deliver(frm="p@example.com", subject="bag", body="My bag did not arrive in Chicago, where is my bag?")
    poll(box)
    [did] = list(g.draftbox)
    g.deliver(frm="p@example.com", subject="Re: bag", body="Still no bag, where is my bag?",
              thread=g.msgs[first]["threadId"])
    poll(box)
    assert list(g.draftbox) == [did]          # replaced in place, not a second draft

    g.edit_draft(did, "I'm looking into this personally.")
    g.deliver(frm="p@example.com", subject="Re: bag", body="Hello?? where is my bag?", thread=g.msgs[first]["threadId"])
    poll(box)
    assert list(g.draftbox) == [did]
    assert "personally" in g.draft_body(did)  # the person's edit is untouched


def test_thread_a_person_already_answered_is_skipped(box):
    g = box[0]
    mid = g.deliver(frm="p@example.com", subject="bag", body="My bag did not arrive in Chicago, where is my bag?")
    reply_msg = EmailMessage()
    reply_msg["From"], reply_msg["To"], reply_msg["Subject"] = g.me, "p@example.com", "Re: bag"
    reply_msg.set_content("Answered already by a person.")
    g._store("human1", reply_msg, g.msgs[mid]["threadId"], ["SENT"])
    r = poll(box)
    assert r.skipped == 1 and g.draftbox == {}
