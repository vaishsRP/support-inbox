import json

from fastapi.testclient import TestClient

from inbox.app import create_app
from inbox.llm import StubModel


def reply(system, user):
    if "SOURCES" in user:
        return json.dumps({"fits": True, "reply": "The wifi is being fixed. [source: Wifi outage]", "uncovered": []})
    if "adapt" in system:
        return json.dumps({"fits": True, "reply": "Please ask a flight attendant to reset the system. Someone will call you Friday.", "uncovered": []})
    return json.dumps({"questions": ["Which room?"], "issue": "unclear"})


def client(firm):
    return TestClient(create_app(firm=firm, model=StubModel(reply), demo=True))


def sid(c):
    return c.get("/demo/api/session").json()["sid"]


def test_page_and_redirect(firm):
    c = client(firm)
    assert "Support inbox" in c.get("/demo").text
    assert c.get("/", follow_redirects=False).status_code == 307


def test_visitor_flow_send_draft_reply_track(firm):
    c = client(firm)
    s = sid(c)
    r = c.post("/demo/api/send", json={"sid": s, "subject": "wifi", "body": "wifi not working on my flight"}).json()
    assert r["mail"]["route"] == "reuse" and r["mail"]["draft"].startswith("[[REVIEW")
    [mail] = c.get(f"/demo/api/inbox?sid={s}").json()
    out = c.post("/demo/api/sent", json={"sid": s, "draft_id": mail["id"],
                                         "text": "Please ask a flight attendant to reset it. Someone will call you Friday."}).json()
    assert out["outcome"] == "edited" and out["pair_id"] is not None   # joins this visitor's own pool
    acts = c.get(f"/demo/api/actions?sid={s}").json()
    assert [a["kind"] for a in acts] == ["commitment"]


def test_visitors_cannot_see_each_other(firm):
    c = client(firm)
    a, b = sid(c), sid(c)
    c.post("/demo/api/send", json={"sid": a, "body": "wifi not working on my flight"})
    assert c.get(f"/demo/api/inbox?sid={b}").json() == []
    [mail] = c.get(f"/demo/api/inbox?sid={a}").json()
    assert c.post("/demo/api/sent", json={"sid": b, "draft_id": mail["id"], "text": "hi"}).status_code == 404


def test_notes_are_private_to_the_visitor(firm):
    c = client(firm)
    a, b = sid(c), sid(c)
    c.post("/demo/api/note", json={"sid": a, "title": "Wifi outage", "body": "wifi not working on flights today, fix by noon"})
    assert any(d["title"] == "Wifi outage" for d in c.get(f"/demo/api/context?sid={a}").json())
    assert not any(d["title"] == "Wifi outage" for d in c.get(f"/demo/api/context?sid={b}").json())
    ra = c.post("/demo/api/send", json={"sid": a, "body": "wifi not working on flights today"}).json()
    rb = c.post("/demo/api/send", json={"sid": b, "body": "wifi not working on flights today"}).json()
    assert ra["mail"]["route"] == "docs"
    assert rb["mail"]["route"] != "docs"


def test_bad_session_rejected(firm):
    c = client(firm)
    assert c.get("/demo/api/inbox?sid=x").status_code == 400




def test_sent_reply_teaches_the_next_draft_for_that_visitor_only(firm):
    seen = []

    def spy(system, user):
        seen.append(user)
        return reply(system, user)

    c = TestClient(create_app(firm=firm, model=StubModel(spy), demo=True))
    a, b = sid(c), sid(c)
    c.post("/demo/api/send", json={"sid": a, "body": "wifi not working on my flight"})
    [mail] = c.get(f"/demo/api/inbox?sid={a}").json()
    c.post("/demo/api/sent", json={"sid": a, "draft_id": mail["id"],
                                   "text": "So sorry! Wifi is back after a restart of the seat screen. Unique-phrase-xyz."})
    seen.clear()
    c.post("/demo/api/send", json={"sid": a, "body": "wifi not working on my flight"})
    assert any("Unique-phrase-xyz" in u for u in seen)      # A's own reply is now the approved answer
    seen.clear()
    c.post("/demo/api/send", json={"sid": b, "body": "wifi not working on my flight"})
    assert not any("Unique-phrase-xyz" in u for u in seen)  # B never sees it
