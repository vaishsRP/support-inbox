"""A small in-memory stand-in for the Gmail API, shaped like googleapiclient's:
service.users().messages().get(userId=..., id=...).execute(). Enough of it to test the
poller end to end without a real account."""

from __future__ import annotations

import base64
import itertools
from email.message import EmailMessage


class _Call:
    def __init__(self, fn):
        self.fn = fn

    def execute(self):
        return self.fn()


class NotFound(Exception):
    def __str__(self):
        return "HttpError 404 notFound"


class FakeGmail:
    def __init__(self, me: str = "support@harbourbrook.example"):
        self.me = me
        self.msgs: dict[str, dict] = {}
        self.draftbox: dict[str, dict] = {}
        self.labelmap = {"INBOX": "INBOX", "SENT": "SENT", "DRAFT": "DRAFT"}
        self.hid = 100
        self._ids = itertools.count(1)
        self._clock = itertools.count(int(__import__("time").time() * 1000), 60_000)
        self.added: list[tuple[int, str]] = []

    # ---- helpers for tests -----------------------------------------------------------
    def team_replied(self, thread: str, body: str, to: str = "x@example.com", subject: str = "Re: x") -> str:
        """A reply the team sent in the past, straight into the Sent folder."""
        m = EmailMessage()
        m["From"], m["To"], m["Subject"] = self.me, to, subject
        m.set_content(body)
        return self._store(f"s{next(self._ids)}", m, thread, ["SENT"])

    def deliver(self, *, frm: str, subject: str, body: str, thread: str | None = None, headers: dict | None = None,
                in_reply_to: str | None = None) -> str:
        m = EmailMessage()
        m["From"], m["To"], m["Subject"] = frm, self.me, subject
        mid = f"m{next(self._ids)}"
        m["Message-ID"] = f"<{mid}@mail.example>"
        if in_reply_to:
            m["In-Reply-To"] = in_reply_to
        for k, v in (headers or {}).items():
            m[k] = v
        m.set_content(body)
        return self._store(mid, m, thread or f"t{mid}", ["INBOX"])

    def human_sends(self, draft_id: str, text: str | None = None) -> str:
        """What a person does in Gmail: edit the draft (optionally) and press send."""
        d = self.draftbox.pop(draft_id)
        raw = base64.urlsafe_b64decode(d["message"]["raw"])
        from email import message_from_bytes, policy

        m = message_from_bytes(raw, policy=policy.default)
        if text is not None:
            m.set_content(text)
        # Like Gmail: the sent message keeps the draft's message id.
        mid = d["message"].get("id") or f"s{next(self._ids)}"
        return self._store(mid, m, d["message"].get("threadId") or f"t{mid}", ["SENT"])

    def draft_body(self, draft_id: str) -> str:
        from email import message_from_bytes, policy

        raw = base64.urlsafe_b64decode(self.draftbox[draft_id]["message"]["raw"])
        return message_from_bytes(raw, policy=policy.default).get_content()

    def _subject(self, raw: str) -> str:
        from email import message_from_bytes, policy

        return str(message_from_bytes(base64.urlsafe_b64decode(raw), policy=policy.default)["Subject"])

    def draft_subject(self, draft_id: str) -> str:
        from email import message_from_bytes, policy

        raw = base64.urlsafe_b64decode(self.draftbox[draft_id]["message"]["raw"])
        return str(message_from_bytes(raw, policy=policy.default)["Subject"])

    def edit_draft(self, draft_id: str, text: str) -> None:
        from email import message_from_bytes, policy

        raw = base64.urlsafe_b64decode(self.draftbox[draft_id]["message"]["raw"])
        m = message_from_bytes(raw, policy=policy.default)
        m.set_content(text)
        self.draftbox[draft_id]["message"]["raw"] = base64.urlsafe_b64encode(m.as_bytes()).decode()

    def _store(self, mid, msg, thread, labels) -> str:
        self.hid += 1
        self.msgs[mid] = {
            "id": mid, "threadId": thread, "labelIds": list(labels), "internalDate": str(next(self._clock)),
            "raw": base64.urlsafe_b64encode(msg.as_bytes()).decode(), "history": self.hid,
        }
        if "INBOX" in labels:
            self.added.append((self.hid, mid))
        return mid

    # ---- the API surface -------------------------------------------------------------
    def users(self):
        return self

    def getProfile(self, userId):
        return _Call(lambda: {"emailAddress": self.me, "historyId": str(self.hid)})



class _Messages:
    def __init__(self, g: FakeGmail):
        self.g = g

    def list(self, userId, q="", maxResults=100, pageToken=None, includeSpamTrash=False):
        def run():
            if "in:sent" in q:
                found = [m for m in self.g.msgs.values() if "SENT" in m["labelIds"]]
                if 'subject:"' in q:
                    want = q.split('subject:"', 1)[1].split('"', 1)[0]
                    found = [m for m in found if want in self.g._subject(m["raw"])]
            else:
                found = [self.g.msgs[m] for _, m in self.g.added]
            found = sorted(found, key=lambda m: int(m["internalDate"]), reverse=True)   # newest first, like Gmail
            return {"messages": [{"id": m["id"], "threadId": m["threadId"]} for m in found]}
        return _Call(run)

    def get(self, userId, id, format="raw"):
        def run():
            if id in self.g.msgs:
                return dict(self.g.msgs[id])
            for d in self.g.draftbox.values():
                if d["message"].get("id") == id:
                    return dict(d["message"], labelIds=["DRAFT"], internalDate=d["at"])
            raise KeyError(id)
        return _Call(run)

    def modify(self, userId, id, body):
        def run():
            self.g.msgs[id]["labelIds"] += body.get("addLabelIds", [])
            return {}
        return _Call(run)


class _History:
    def __init__(self, g: FakeGmail):
        self.g = g

    def list(self, userId, startHistoryId, historyTypes=None, labelId=None, pageToken=None):
        start = int(startHistoryId)
        return _Call(lambda: {
            "history": [{"messagesAdded": [{"message": {"id": m}}]} for h, m in self.g.added if h > start],
            "historyId": str(self.g.hid),
        })


class _Threads:
    def __init__(self, g: FakeGmail):
        self.g = g

    def get(self, userId, id, format="full"):
        # Like the real API: threads cannot be fetched raw.
        if format not in ("full", "metadata", "minimal"):
            raise ValueError(f'Parameter "format" value "{format}" is not an allowed value')
        msgs = [dict(m) for m in self.g.msgs.values() if m["threadId"] == id]
        msgs += [dict(d["message"], labelIds=["DRAFT"], internalDate=d["at"]) for d in self.g.draftbox.values()
                 if d["message"].get("threadId") == id]
        return _Call(lambda: {"id": id, "messages": msgs})


class _Drafts:
    def __init__(self, g: FakeGmail):
        self.g = g

    def create(self, userId, body):
        def run():
            did = f"d{next(self.g._ids)}"
            self.g.draftbox[did] = {"id": did, "message": dict(body["message"], id=f"dm{did}"), "at": str(next(self.g._clock))}
            return {"id": did}
        return _Call(run)

    def update(self, userId, id, body):
        def run():
            self.g.draftbox[id]["message"] = dict(body["message"], id=f"dm{id}")
            return {"id": id}
        return _Call(run)

    def get(self, userId, id, format="raw"):
        def run():
            if id not in self.g.draftbox:
                raise NotFound()
            return self.g.draftbox[id]
        return _Call(run)


class _Labels:
    def __init__(self, g: FakeGmail):
        self.g = g

    def delete(self, userId, id):
        def run():
            self.g.labelmap = {n: i for n, i in self.g.labelmap.items() if i != id}
            return {}
        return _Call(run)

    def list(self, userId):
        return _Call(lambda: {"labels": [{"name": n, "id": i} for n, i in self.g.labelmap.items()]})

    def create(self, userId, body):
        def run():
            self.g.labelmap[body["name"]] = f"L{len(self.g.labelmap)}"
            return {"id": self.g.labelmap[body["name"]]}
        return _Call(run)


def install_resources(g: FakeGmail) -> FakeGmail:
    """Attach the resource methods the way googleapiclient exposes them."""
    g.__dict__["messages"] = lambda: _Messages(g)
    g.__dict__["history"] = lambda: _History(g)
    g.__dict__["threads"] = lambda: _Threads(g)
    g.__dict__["drafts"] = lambda: _Drafts(g)
    g.__dict__["labels"] = lambda: _Labels(g)
    return g


def fake_service(me: str = "support@harbourbrook.example") -> FakeGmail:
    g = FakeGmail(me)
    install_resources(g)
    return g
