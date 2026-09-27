# An email assistant for support teams that won't promise what nobody authorised

Live demo: *link goes here once the Hugging Face Space is up* (see [deploy/DEPLOY.md](deploy/DEPLOY.md))

Support tools close tickets. This one tracks what you promised.

It reads incoming support mail and leaves a draft reply for a person to check and
send. The draft is adapted from a reply the team already sent to a similar email, or
written from the company's policy pages. If neither is close, it writes nothing and
says why. Anything that would commit the company (a refund, money, a deadline, an
exception, an admission of fault) is left as a red placeholder for someone who is
allowed to decide it. Promises that do go out land on an action list with their
deadline, next to things to check and spikes of one problem across many customers.

It never sends anything. There is no code that can, and a test fails the build if
any appears.

Short answer from building it:

- **The safety side works.** In every realistic test, including one where the email
  told the model it was "authorised by management", no promise got past the rules.
  Legal threats and data-deletion requests were never drafted. Refusals came with
  related past emails and questions to ask the customer.
- **Reusing past answers is weaker than I expected on real data.** On American
  Airlines' public Twitter support, customers ask the same things over and over, but
  the airline writes every reply fresh: the answer to the closest earlier question is
  barely closer to the real reply than a random one (0.41 against 0.39 character
  similarity). Whether a person would still accept those answers is what the hand
  labels are for, and they are not done yet.
- **Spike detection is useful but noisy.** It found a plausible real surge (delays on
  the Monday after Thanksgiving) and also flagged chatter.

<!-- APPLE -->

## Try it

The demo pretends to be **Harbourbrook Student Homes**, a made-up company renting
rooms to international students. Write to it as a student, then switch to the
support team's side: a Gmail-style inbox with the draft, the labels, the action list
and a page for incident notes. Everything about the company is invented and labelled
as such; the real evaluation data never appears in the demo.

## Why this and not Zendesk

Drafting from past tickets is not new: Zendesk, Intercom and Freshdesk all do it.
What is uncommon here:

- **It asks what a draft commits to, not only whether it is accurate.** The same
  rule file blocks the promises nobody authorised and turns the allowed ones into
  action-list rows with a deadline. It is a plain file a support lead can read and
  edit, not a model.
- **It refuses on purpose.** Returning nothing, with a reason, looks bad in a demo,
  which is why products rarely do it. Drafting confidently on questions nobody has
  answered before is where bad answers come from.
- **It turns the inbox into work:** promises to keep, checks to make, and a spike
  alert when many customers write about one thing, with a nudge to write a short note
  that the next drafts will use.
- **It measures how much a person changed the draft before sending**, not how many
  tickets closed without a human.

## What I found

**Customers repeat themselves; this airline's answers do not.** For each real
question I looked only at earlier ones (no peeking at the future), took the closest,
and compared its approved answer with the reply actually sent. Near copies came up in
0 to 3% of cases even for near-identical questions. Full report:
[reports/step1_americanair.md](reports/step1_americanair.md).

**Sarcasm fools the search.** "Thanks for losing my bag!" sits right next to "Thanks
for getting my luggage back to me!". The fit check (a second look by the model before
reusing an answer) is what stops the wrong reply.

**Dated notes have to beat old answers.** In the first realistic test, an incident
note about a new app error lost to a two-month-old answer that only looked similar.
A live note now wins, because it describes what is happening now.

**The commitment rules barely fire, which is the point.** Run over all 28,735 real
airline replies they flag under 1%. Every false alarm found ("they'll issue you a new
boarding pass" read as a refund) became a regression test. The softest miss in a
sample was "we'll reunite you with your belongings as early as possible".

Realistic scenarios: [reports/scenarios_americanair.md](reports/scenarios_americanair.md).
Everything that changed and why: [DECISIONS.md](DECISIONS.md). The honest overall
assessment: [reports/sanity_check.md](reports/sanity_check.md).

## What is missing

- **Hand labels.** About 150 pairs judged by a person ("would this earlier answer
  have done?") to set the reuse threshold and test it. The tool for it is built
  (`python -m inbox label`); the labelling is not done.
- **Real edits.** Whether people's corrections make later drafts need less editing
  needs people editing drafts: a small hand-run study of 30 + 30 drafts.
- **Gmail.** Drafts into threads and `AI/*` labels is the last build step and not
  done. The demo imitates it.
- **Real email.** The development data is tweets, so there is no handling of quoted
  text, signatures, attachments or auto-replies. That is the first thing a real
  mailbox needs.

## How it works

```
incoming mail
  ├─ incoming rules: legal threat, data request, chargeback, press, safety  →  no draft, route to a person
  ├─ closest earlier questions (local embeddings, only the past)
  │    ├─ a live incident note matches          →  draft from the note, cite it
  │    ├─ a past answer fits                    →  adapt it
  │    ├─ a policy page fits                    →  draft from it, cite it
  │    └─ nothing fits                          →  no reply; related emails + questions to ask
  └─ outgoing rules on the draft: promise beyond authority  →  [[NEEDS AUTHORITY: ...]]

sent reply  →  diff against the draft  ·  promises become action-list rows  ·  joins the answer pool
```

Everything in double brackets is for the agent, never the customer: `[[name]]`,
`[[CHECK: what to look up]]`, `[[NEEDS AUTHORITY: refund, ask billing lead]]`,
`[[AGENT NOTE: ...]]`. A bracket that reaches a customer is counted as a miss.

Data: the public *Customer Support on Twitter* dataset (Kaggle, CC BY-NC-SA 4.0),
American Airlines and Apple Support. No affiliation with either is implied. Public
datasets of real support emails with real agent replies basically do not exist,
because companies cannot publish customer mail; the email-shaped datasets on Kaggle
and Hugging Face are generated by language models and cannot say whether real
customers repeat themselves.

## Run it

Python 3.11+. Everything is free: local embeddings, Groq's free tier (or Ollama for a
local model), SQLite.

```bash
python -m venv .venv
.venv\Scripts\activate                    # Windows; source .venv/bin/activate elsewhere
pip install torch --index-url https://download.pytorch.org/whl/cpu
pip install -e ".[embed,dev]"
copy .env.example .env                     # then paste a free Groq key from console.groq.com
```

The dataset is not in the repo (licence). Download `twcs.csv` from Kaggle into
`data/raw/twcs/`, then:

```bash
python -m inbox --firm americanair import       # load the corpus
python -m inbox --firm americanair explore      # how repetitive is it?
python -m inbox --firm americanair scenarios    # realistic end-to-end tests
python -m inbox --firm americanair replay       # the whole pipeline on real messages
python -m inbox --firm americanair spikes       # spike detection over the timeline
python -m inbox --firm americanair label        # hand-label pairs (resumable)
python -m inbox --firm demo import              # the made-up demo company
python -m inbox.app --firm demo --demo          # the demo on http://127.0.0.1:8000/demo
pytest -q                                       # no model downloads, no network
```

## Limitations

- Tweets, not emails, and only two brands, both large American companies in 2017.
- Thresholds are provisional until labelled; the embedding model squeezes most
  similarities into 0.85 to 0.95, so a guessed threshold is expensive.
- Categories come from clustering tweets and group by mood as well as by problem,
  which makes spike detection noisier than it would be on email.
- The commitment rules are English; other languages are checked through a
  translation, which costs a model call and can fail (the draft is then held).
- The demo's company and emails are written by me. It shows the behaviour; it is not
  evidence that it works.
- Depends on whichever model Groq still hosts. Groq retired the one I planned to use
  before I started; `LLM_MODEL` in `.env` changes it.

## Later list

New ideas go here, not into the build: mail hygiene for real email (quoted text,
signatures, auto-replies, attachments); Gmail drafts and labels; checks that answer
themselves (read-only lookups into billing or booking systems); Outlook and helpdesk
connectors; a question answered many times becomes a suggested help-centre article;
spike alerts to Slack; a model pass over commitments if the rules visibly miss
things; styled email templates.

## Licence

Code and the made-up demo company: MIT. The Twitter dataset and anything quoted from
it in `reports/` and `labels/`: CC BY-NC-SA 4.0, from *Customer Support on Twitter* by
Thought Vector on Kaggle.
