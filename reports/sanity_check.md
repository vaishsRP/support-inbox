# Sanity check: is this practical, feasible, and does it add anything?

Written 2026-09-27 after steps 1, 2 and 4 to 11 were built and run against real data,
before the hand labels (step 3) and Gmail (step 12). It is meant to be read by the
owner before deciding what to finish, and by anyone judging the project.

Short version: **the safety half works and is the real contribution; the
answer-reuse half is unproven on real data and weaker than the spec assumed; the
action-list half is useful but noisy.** Details and evidence below.

<!-- RESULTS -->

## 1. Does it add anything?

**Over doing nothing (a person writes every reply):** yes, if drafts are accepted
with light edits often enough. That is exactly the number this project measures and
does not have yet on real edits (see "What is not known yet").

**Over Zendesk / Intercom / Freshdesk AI drafting:** the drafting itself adds nothing
new, and the README says so. Three things are genuinely different, and one was
verified tonight:

| Claim | Status |
|---|---|
| Never promises what nobody authorised: money, refunds, exceptions, deadlines become `[[NEEDS AUTHORITY]]` placeholders | **Verified** on every scenario and replay draft, including a prompt injection that told the model it was "authorised by management". Rules fire on under 1% of real replies, so they do not bury agents. |
| Refuses with a reason instead of guessing, and still helps the agent | **Verified**. Refusals came with related past threads and questions to ask the customer, in the customer's language. |
| Turns the inbox into an action list: promises with deadlines, checks, spikes | **Partly**. Promises and checks work. Spike detection found a plausible real surge (post-Thanksgiving delays) but also noise, because clusters built from tweets group by mood as much as by problem. |
| Measures how much a human changed the draft, not "deflection rate" | **Built, not yet measured** on real human edits. Needs the 30 + 30 hand-run study in the spec. |

## 2. Is it practical for a real support team?

- **The team stays in Gmail.** No new tool to learn except two small pages. That is
  the right call and costs nothing to keep.
- **Setup effort for a new firm:** import the mailbox history, upload policy pages,
  label about 150 pairs (roughly an hour), review the rule file. An afternoon.
- **Where it would annoy people:** confidence bands depend on thresholds that must be
  labelled per firm; until then many mails are refused. A team that expects a draft
  for everything will read refusals as failure, which is the spec's point but still a
  change-management problem.
- **Where it would fail quietly:** replies that say the same thing in new words look
  "different" to every automatic measure here. The only defence is the hand labels and
  the hand-run edit study.

## 3. Is it feasible, technically and at zero cost?

- **Runs free:** local embeddings, Groq's free tier (about 1,000 requests a day on the
  large model, then a second model), SQLite, Hugging Face Spaces for the demo. A support
  inbox of 60 mails a day fits comfortably; 1,000 a day would not on the free tier.
- **Laptop limits hit:** embedding 100k tweets took most of an hour on CPU. A real
  firm's mailbox (thousands, not hundreds of thousands) is minutes.
- **Model churn is real:** Groq retired the model the plan named before a line was
  written. The provider is configuration, which is the only defence.
- **Gmail:** not built yet. Restricted-scope verification is the real obstacle for
  anything beyond a personal demo account.

## 4. Legal and ethical checks

- Real customer mail is personal data. The demo uses a made-up company; the
  development data is public tweets under CC BY-NC-SA, not redistributed.
- Personal details in stored answers are replaced with placeholders at import, so a
  reused answer cannot carry one customer's name or booking reference to another.
  Verified on accented and Dutch/German greetings after a miss was found.
- The free Groq tier should never receive a real firm's customer mail; use a local
  model for that. The code supports it; it is one setting.
- The tool cannot send mail, enforced by a test that fails the build.

## 5. What is not known yet

1. Whether reuse works when judged by a person (step 3 labels).
2. Whether human corrections make later drafts need fewer edits (the 30 + 30 study).
3. How many real commitments the rules miss (hand check on a sample, spec metric 3).
4. Whether Gmail's restricted scope is workable beyond testing mode.

## 6. Recommended changes (logged in DECISIONS.md)

<!-- CHANGES -->
