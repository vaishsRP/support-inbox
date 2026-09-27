import json

from fastapi.testclient import TestClient

from inbox.app import create_app
from inbox.llm import StubModel


def client(firm, reply='{"fits": true, "reply": "Please file a report with our Baggage team.", "uncovered": []}'):
    return TestClient(create_app(firm=firm, model=StubModel(reply)))


def test_pages_render(firm):
    c = client(firm)
    assert "Action list" in c.get("/").text
    assert "Add a dated note" in c.get("/context").text


def test_mail_to_draft_to_sent_to_action_list(firm):
    c = client(firm)
    r = c.post(
        "/api/mail",
        json={
            "msg_id": "m-new",
            "thread_id": "t-new",
            "customer_id": "c-new",
            "text": "My bag did not arrive in Chicago, where is my bag?",
            "received_at": "2017-10-20T10:00:00+00:00",
        },
    ).json()
    assert r["route"] == "reuse" and r["draft"].startswith("[[REVIEW")
    s = c.post(
        "/api/sent",
        json={
            "draft_id": r["draft_id"],
            "text": "Please file a report with our Baggage team. Someone will call you Friday.",
            "sent_at": "2017-10-20T11:00:00+00:00",
        },
    ).json()
    assert s["outcome"] == "edited"
    assert s["pair_id"] is not None  # the sent reply joined the answer pool
    rows = c.get("/api/actions").json()
    assert rows and rows[0]["kind"] == "commitment"
    assert "Someone will call you Friday" in c.get("/").text


def test_context_note_and_action_done(firm):
    c = client(firm)
    c.post("/context/note", content="title=Login+broken&body=Use+version+2.0.1&days=7",
           headers={"content-type": "application/x-www-form-urlencoded"})
    assert "Login broken" in c.get("/context").text
    c.post("/api/mail", json={"msg_id": "m1x", "thread_id": "t1x", "customer_id": "c1x",
                              "text": "My bag did not arrive in Chicago, where is my bag?"})
    # No commitments yet: the digest still renders.
    assert "Support digest" in c.get("/api/digest").text
