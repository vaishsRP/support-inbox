# Support inbox assistant

Support tools close tickets. This one tracks what you promised.

It drafts replies to support mail by adapting answers the team already wrote and
approved, **refuses** (with a reason) when it has nothing close, replaces any
commitment it has no authority to make (refunds, money, exceptions, deadlines,
admissions of fault) with a visible placeholder, and turns what the inbox is
saying into an **action list**: promises that went out, checks an agent has to
make, and spikes of one problem across many customers.

**It never sends mail.** There is no code that can, and a test fails the build if
any appears. The human always presses send.

> Personal portfolio project, autumn 2026. Built and run at zero cost.
> Full design and reasoning: [SPEC.md](SPEC.md).

## What is honest about it

- **Not novel as a category.** Zendesk, Intercom, Freshdesk and others draft from
  past tickets. What is uncommon here: commitment awareness, deliberate refusal,
  turning the inbox into an action list, and measuring how much the human changed
  the draft instead of "deflection rate".
- **The development data is tweets, not mail.** No real support inbox was
  available. The stand-in is the public *Customer Support on Twitter* dataset
  (Kaggle, CC BY-NC-SA 4.0), scoped to American Airlines and Apple Support. No
  affiliation with either company is implied. Tweets have no quoted text,
  signatures, attachments or auto-reply headers, so the mail-hygiene layer a real
  mailbox needs is **not built**; it is the first item on the later list.
- **The replay is not the pitch's evidence.** Replaying the corpus compares
  drafts with the reply the brand actually sent. That shows whether more examples
  help. Whether *human corrections* teach the tool needs people editing drafts,
  which is a separate small hand-run study (see SPEC.md, "What gets measured").

## Results

See [reports/](reports/). Summary to be written once the numbers are in.

## How it works

```
incoming mail
  └─ incoming rules ── legal threat, data request, chargeback, press, safety ──> no draft, route to a person
  └─ retrieve closest earlier questions (local embeddings, only the past)
       ├─ close enough ──> adapt that approved answer            (route: reuse)
       ├─ else a document or dated note is close ──> draft + cite (route: docs)
       └─ else ──> no reply text; related threads + questions to ask the customer (route: refused)
  └─ outgoing rules on the draft ── commitment beyond authority ──> [[NEEDS AUTHORITY: ...]]
  └─ draft opens with a review line; confidence band high / medium / low from calibrated similarity

sent reply (what the human actually sent)
  └─ diff against the draft (the edit measure)
  └─ outgoing rules again: promises typed by hand become action-list rows
  └─ joins the pool of approved answers (the answer-once loop)
```

Everything in double brackets is for the agent, never the customer: `[[name]]`,
`[[CHECK: what to look up]]`, `[[NEEDS AUTHORITY: refund, ask billing lead]]`,
`[[AGENT NOTE: ...]]`. A placeholder that reaches a customer is counted as a miss.

The company-specific parts are plain files a support manager can read and edit:
`config/firms/<firm>.yaml` (corpus, thresholds), `config/rules/default.yaml`
(what must never be drafted, what may not be promised, what gets tracked),
`config/categories/<firm>.yaml` (categories derived by clustering, named by hand).

## Running it

Free throughout: local embeddings, Groq's free tier (or a local Ollama model) for
drafting, SQLite, no hosted services.

```bash
python -m venv .venv && .venv/Scripts/activate      # Windows; source .venv/bin/activate elsewhere
pip install -e ".[embed,dev]"
cp .env.example .env                                  # add your keys; .env is git-ignored

# The dataset is not in the repo (licence). Download twcs.csv from Kaggle into data/raw/twcs/.
python -m inbox --firm americanair import             # step 1: load the corpus
python -m inbox --firm americanair explore            # step 1: how repetitive is it?
python -m inbox --firm americanair retrieve           # step 2: closest past threads, judged by eye
python -m inbox --firm americanair label              # step 3: hand-label pairs (resumable)
python -m inbox --firm americanair calibrate          # step 3: thresholds from the labels
python -m inbox --firm americanair replay             # steps 4-8 end to end, against the corpus
python -m inbox --firm americanair categorize         # step 9: suggested categories
python -m inbox --firm americanair spikes             # step 9: spike detection over the timeline
python -m inbox.app --firm americanair                # the action list and context page on localhost:8000
pytest -q                                             # no model downloads, no network
```

Docker: `docker build -t support-inbox .` then
`docker run --env-file .env -v "$PWD/data:/app/data" -p 127.0.0.1:8000:8000 support-inbox`.

## Before any real deployment

Required, not built: mail hygiene for real mail; deleting everything about one
customer on request; a retention period; a login on the two pages (they listen on
localhost only); Google's restricted-scope verification for Gmail; re-labelling
thresholds on the firm's own mail.

## Later list

New ideas go here, not into the build.

- Mail hygiene: an mbox importer, stripping quoted text, signatures and footers,
  skipping auto-replies, bounces and newsletters, noting attachments.
- Gmail: drafts into threads and `AI/*` labels (spec step 12, next up).
- Checks that answer themselves: read-only lookups into billing, order and status systems.
- Outlook and helpdesk connectors alongside Gmail.
- A question answered many times becomes a suggested help-centre article.
- Spike alerts to Slack or Teams.
- A model pass over commitments, if the rules visibly miss things.
- Closing a commitment automatically when a follow-up goes out in its thread.
- Styled drafts: a light HTML wrapper, later a template designer.

## Licence

Code: MIT. The dataset and anything derived from its text (the hand-labelled
pairs in `labels/`, examples quoted in `reports/`) are CC BY-NC-SA 4.0, from
*Customer Support on Twitter* by Stuart Axelbrooke / Thought Vector on Kaggle.
