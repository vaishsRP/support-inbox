# A support inbox assistant that won't promise what nobody authorised

Demo video: coming soon. To try it yourself, see [Try it](#try-it).

It works inside Gmail. Every customer email gets a draft reply in its thread and one
label saying what to do with it. A person reads it and presses send; the tool has no
code that can send mail, and a test fails the build if any appears.

- **Drafts from what the team already wrote.** A new email is matched to the closest
  earlier question and the reply the team sent then, or to the policy pages. Newest
  answers win, and when old and new replies disagree ("30 days" vs "21 days") the draft
  says so.
- **Never commits on its own.** Refunds, money, deadlines, exceptions and admissions of
  fault become a red `[[NEEDS APPROVAL]]` placeholder. Legal threats, data requests and
  similar get an escalation note and a safe holding reply instead of an answer.
- **Says when it doesn't know.** If nothing covers the question, the draft is a frame
  with `[[WRITE YOUR ANSWER]]` and questions worth asking, never an invented answer.
- **Keeps track of what was promised.** Promises in sent replies, things to look up
  and spikes of one problem land on a to-do list with deadlines.
- **Learns from what is sent.** Every reply a person sends becomes an approved answer,
  so wording and tone carry over.

## In Gmail

| Label | The draft | You |
|---|---|---|
| `AI/draft-ready/high`, `/medium`, `/low` | A reply from past replies or policies | check, send |
| `AI/needs-approval` | A reply with a red placeholder, or an escalation note and holding reply | approve or escalate, send |
| `AI/no-answer` | A frame to write in, with questions to ask | write it, send |
| `AI/skipped` | none: auto-replies, bounces, newsletters | nothing |

Two drafts work as pages you edit and never send:

- **To-do list**: delete a line when it's done, type a line at the bottom to add one.
- **Context and policies**: delete a note to remove it (a false alarm), type a note at
  the bottom for anything happening now ("Heating outage Block C for 3 days").

Also handled: quoted history, signatures and footers are stripped; replies are in the
customer's language (English, Dutch, German); the customer's name comes from how they
sign off; bank and card numbers are hidden from the model; each customer's earlier
conversations are given as dated context; the company's house rules ("use *je*, not
*u*") apply to every draft.

## What I found

Built against public customer-support tweets to American Airlines and Apple Support
(Kaggle, CC BY-NC-SA 4.0), then run on a live Gmail account.

| | American Airlines | Apple Support |
|---|---:|---:|
| Questions with a very close earlier one | 18% | 69% |
| Replayed messages that got a draft | 7% of 60 | 40% of 45 |
| Draft rate as the pool of answers grew | 5% → 15% | 13% → 67% |
| Realistic test scenarios passed | 16 of 17 | 12 of 12 |

- **Reuse works for a team that answers in templates** (Apple) and barely for one that
  writes every reply fresh (American Airlines). That decides who this is for.
- **The safety side held**: no invented promise got through, including an email that
  claimed to be "authorised by management".
- **Spike detection caught a real incident on the day**: the iOS 11.1 bug that turned
  "i" into "A [?]", 16 customers in six hours against a normal of one.
- **Real email found real bugs** the tests had missed: a Gmail API quirk with sent
  drafts, a deleted draft stalling the watcher, "u" (Dutch for "you") switching a reply
  to Dutch. Each is fixed and now tested.

Details: [sanity check](reports/sanity_check.md), [decision log](DECISIONS.md),
[reports](reports/).

## Not done yet

- Hand-labelling ~150 pairs to set the similarity thresholds (they are estimates now).
- A small study of whether people's edits make later drafts need less editing.
- Outlook / Microsoft 365 (Gmail only).

## Try it

Python 3.11+, all free: local embeddings, Groq's free tier or a local Ollama model, SQLite.

```bash
python -m venv .venv && .venv\Scripts\activate
pip install torch --index-url https://download.pytorch.org/whl/cpu
pip install -e ".[embed,gmail,dev]"
copy .env.example .env                       # add a free Groq key
python -m inbox --firm demo import           # a made-up student housing company
python -m inbox.app --firm demo --demo       # web demo on http://127.0.0.1:8000/demo
pytest -q                                    # 129 tests, no network needed
```

With your own Gmail: [deploy/GMAIL_SETUP.md](deploy/GMAIL_SETUP.md) (about 20 minutes).
For a company's support inbox: [deploy/AT_A_COMPANY.md](deploy/AT_A_COMPANY.md).

## Licence

Code and the made-up demo company: MIT. Tweet data and anything quoted from it in
`reports/`: CC BY-NC-SA 4.0, from *Customer Support on Twitter* (Thought Vector, Kaggle).
