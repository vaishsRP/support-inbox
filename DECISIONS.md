# Decision log

What changed, when, and what prompted it. Newest first. Findings that could
change the project's direction are marked **finding**.

## 2026-09-27 (evening): Gmail in daily use

Lessons from running it on a real Gmail account, each fixed and tested:

- **Every email gets a draft**, changing the spec's "refuse means no draft": a refusal is
  now a frame with `[[WRITE YOUR ANSWER]]` and questions to ask (still no invented
  answer), and a blocked email gets an escalation note plus the firm's holding reply.
  So every email is worked the same way in Gmail. Skeletons do not count as drafted in
  the measures, and a sent skeleton is "no draft", not an "edit".
- **One label per email**, confidence as a sub-label; `AI/automated` renamed
  `AI/skipped` because it read as "replies are automated".
- **To-do list and Context and policies are editable drafts**: delete a line to close or
  remove, type at the bottom to add. Edits are read once unchanged for a whole check;
  each version is numbered so a stale open copy cannot close rows it never showed; a
  page sent by mistake is read and replaced; the same edit read twice adds nothing.
- **Gmail API facts learned the hard way**: threads cannot be fetched raw; a sent draft
  still answers for its old id (without the DRAFT label); a sent draft's message id
  disappears as a draft. The fake Gmail used by the tests now behaves the same way.
- **The watcher never stalls on one bad message**, and mail moved into the inbox ("Not
  spam") is picked up.
- **Language detection needs real evidence**: "u" (Dutch for "you") had switched an
  English reply to Dutch.
- **Borrowed from similar tools**: house rules in plain language (Intercom Fin Guidance)
  and hiding bank and card numbers from the model (Zendesk redaction).
- **Escalations are specific**: legal threats, data requests, chargebacks, press and
  safety each get their own advice; data requests get a to-do with the GDPR deadline.

## 2026-09-27 (night, later)

- **Bug: re-importing history deleted live mail.** Both importers ran `DELETE FROM
  messages` before loading the corpus, so re-importing the demo company's history wiped
  the text of every visitor email received before it (the drafts survived, the emails
  they answered did not). In a real deployment that would have erased every Gmail
  message the tool had seen. Importers now delete only their own rows (corpus ids are
  prefixed `corpus-`; tweets are short all-digit ids), with a test that live and visitor
  mail survive a re-import. The 14 demo emails already emptied were removed.
- **Renamed `AI/needs-authority` to `AI/needs-approval`**, and the placeholder
  `[[NEEDS AUTHORITY: ...]]` to `[[NEEDS APPROVAL: ...]]`, at the owner's request: it
  says what the label is for, the team lead's queue of things someone must approve.

## 2026-09-27 (night): towards shipping

The owner asked for drafts that could go out as they are, the real Gmail integration,
and mail hygiene built rather than deferred.

- **Mail hygiene is in the build again**, reversing the review's cut, because Gmail
  delivers real email: quoted history (English, Dutch, German, French, Spanish, Outlook
  header blocks), signatures and "Sent from my iPhone" lines, legal footers, auto-replies,
  bounces and newsletters (never drafted), and attachments (noted, not read). Tested on
  hand-built raw messages; the tweet corpus still cannot exercise it.
- **Drafts are laid out as email**: greeting with the customer's first name, short
  paragraphs, the firm's sign-off on its own lines, in the customer's language
  (English, Dutch, German), from `email_style` in the firm config. The name comes from
  how the customer signs off, else their display name, else a firstname.lastname
  address; otherwise "Hi," rather than a guessed name.
- **Only the review line and action-needed warnings stay inside a draft.** "Closest past
  answer is from ..." and source citations moved to the dashboard: they made a ready
  draft look unfinished.
- **Old replies carried their moment along.** A reused heating reply said "that's no fun
  in February" in September. Telling the model to drop such details did nothing until it
  was also told today's date and when the old reply was written.
- **Deadlines are computed in the firm's time zone** and stored in UTC. "Someone will
  come today" sent at 23:30 in Amsterdam was due the next day at 01:59.
- **Sent replies teach tone as well as content.** They were already stored as approved
  answers; now the greeting and sign-off are stripped before storing (so the next draft
  greets its own customer), and drafts written from documents get the team's two most
  recent replies as tone examples. In the demo, each visitor's sent replies join their
  own pool, so the loop can be seen: edit a reply, ask again, get your version back.
- **Gmail is built** (`src/inbox/gmail.py`): reads new mail from the history API
  starting from the moment it is switched on, drafts replies into the thread with the
  right In-Reply-To and References headers, labels, replaces its own untouched draft when
  the customer writes again but never an edited one, skips threads a person already
  answered, captures what was sent, and drafts the morning digest to the manager. Tested
  end to end against an in-memory fake of the Gmail API (7 tests). Not yet run against a
  real account: that needs the owner's demo Gmail and a Google Cloud client.
- **Demo:** action list and context page moved to small boxes at the bottom of the
  sidebar; the no-answer view shows one reason and at most two questions; examples now
  put a check and a promise on the action list; the assistant's status is one quiet line.

## 2026-09-27 (late)

**finding: Apple Support is where reuse holds up; build on Apple.** Step 1 on Apple
(`reports/step1_applesupport.md`): 69% of first messages have a very close earlier
question, and unlike American Airlines the answer gets more reusable as the question
gets closer: near copies rise from 3% to 17% at the top similarity band, and reused
answers score 0.50 against 0.42 for a random one. Still, even at the top 83% are not
near copies, so a person's judgement (step 3 labels) decides. Apple's scenarios passed
12 of 12, and its drafts were the team's real templates (the iOS 11 "I" bug got
Apple's actual workaround reply). American Airlines stays as the contrast case: a team
that writes every reply fresh gets little from reuse.

**finding: American Airlines replay drafted 7%.** 60 real messages: 56 refused, 4
drafted, and the drafts were no closer to the real replies than reusing the old answer
word for word (0.40 to 0.43). At the provisional threshold, on this brand, the tool is
mostly a refusal machine with good notes.

**Stale "situation" answers are flagged.** The replay reused "we fully expect to avoid
cancellations" for a worried customer weeks later: not a commitment by the rules, but
possibly false today. A reused answer that describes a situation ("we're aware",
"known issue", "we expect", "right now", and after the Apple replay reused "the latest
version of iOS 11.0.2" out of date, "latest version") and is more than 3 days old now
gets an agent note to check it is still true, and loses high confidence. Unit test added.

**finding: Apple replay drafted 40%, and the draft rate rose with the pool.** 45 real
messages: 18 drafted, several word for word what Apple sent (workaround link included,
after the link fix). Across three time slices the draft rate went 13%, 40%, 67% as the
pool grew from about 4,500 to 39,000 answers. That is the honest version of the
replay curve: more examples help. 9 of the 27 messages Apple itself only deflected
("DM us") got a real draft. None of the 22 replay drafts across both brands tried to
promise anything, so the replay is not evidence for the commitment rules; the
scenarios and the full-corpus rule run are.

**Bug fixed: reused answers lost their links.** Cleaning turned every URL into `[link]`,
which is right for customer messages but wrong for answers, where the link is often the
answer (Apple's workaround article). Answers now keep URLs, and URLs are shielded from
redaction so a short-link code is never mistaken for a booking reference. Both brands
re-imported; pair ids and questions were fingerprinted before and after and matched,
so cached vectors stayed valid.

**finding: spike detection caught a real incident on time.** On Apple, the first spike
opened on Tuesday 31 October 2017 at 20:00 ("letter / type / weird", 16 customers
against a normal of 1.0), the day iOS 11.1 shipped with the "I" autocorrect bug. It
peaked at 121 customers in one window on 4 November. On American Airlines, a delay
surge on Monday 27 November (after Thanksgiving) was caught.

**Spikes are grouped into incidents by time.** The iOS bug fired in about eight
categories at once (people describe it in different words and different tempers), 43
rows in total. Grouping by meaning failed: every spike centroid sat between 0.94 and
0.99, and unrelated spikes were closer than parts of the same incident. Spikes that
overlap in time are now one incident row listing its topics: 43 rows became 13 on
Apple, 10 became 6 on American Airlines. Coincident but unrelated problems will share a
row; the row lists both.

**Probable data artefacts, not incidents:** both brands "spike" on Monday 9 October and
Monday 23 October around 20:00 to 22:00. Two unrelated companies spiking in the same
windows points at how the dataset was collected. Treated as noise, not as evidence.

**Demo decisions.**
- Made-up company Harbourbrook Student Homes, student housing, written for the demo and
  labelled as such. The owner works in student mobility, so nothing from that employer
  is used; moving the tool there later is a config file, the employer's mailbox, and
  about 150 labels, with the employer's permission and a local model.
- Not built from the suggested generated datasets (Hugging Face tickets, Kaggle
  Aetheros emails, Bitext): each is made up by someone else, and none is one coherent,
  relatable company whose answers agree with its policies. RSiCS was also checked: real
  customers, but talking to chatbots, with no human replies to reuse. MSDialog
  (Microsoft Community forum threads answered by staff) is the one real alternative
  worth keeping in reserve.
- Hosted on a free Hugging Face Space: the real pipeline runs there (the model plus
  PyTorch is far over Vercel's free function limit). Deploy files in `deploy/`.
- Each visitor has a private session; notes are scoped to it; what a visitor "sends"
  never joins the shared answer pool, so nobody can plant an answer for the next visitor.
- The page imitates Gmail's layout and colours after the first two designs were judged
  cluttered and unintuitive by the owner. Corners are square throughout, at the owner's
  choice, with Gmail's layout and colours kept.

## 2026-09-27

**Realistic scenarios added** (`python -m inbox --firm <firm> scenarios`, report in
`reports/scenarios_<firm>.md`): hand-written messages through the whole pipeline
with the real model. First run on American Airlines, 14 of 17 checks passed.
Blocking (legal threat, GDPR), prompt injection, refusing a made-up question and
refusal notes all behaved. Two changes came out of it:

- **Dated notes now win over old answers.** With an incident note for a new app
  error, the drafter still reused a two-month-old answer because it was checked
  first. A live note that matches now goes first: it describes what is happening
  now. Unit test added.
- **The "does this old answer fit?" instruction is stricter** (same specific
  problem, not just the same topic; if in doubt, no).
- **Correction to my own reading:** I first called the reused "check in at the
  kiosk" answer to an app check-in error a wrong answer. It was not: the earlier
  customer had the same app error and that was the airline's real reply. The
  scenario's expectation was wrong and now checks the right thing: without the
  note, the draft must not know the note's workaround.
- The lost-bag message scored 0.929 against a provisional threshold of 0.93 and was
  refused. That is the threshold's fault, not the lost bag's: until the hand
  labels exist the threshold is a guess, and the squashed similarity scale makes
  a guess expensive.

**Considered and not used as the development corpus:** Hugging Face
`Tobi-Bueck/customer-support-tickets` (about 20k tickets, EN/DE, CC BY-NC 4.0) and
Kaggle `rtweera/customer-care-emails` (email threads for a made-up company, GPL 3).
Both are generated by a language model, so every ticket is different by
construction and the answers are model prose: they cannot say whether real
customers repeat themselves. Kept in mind for the demo, which needs a made-up
company anyway.

**finding: on American Airlines, answers are not reusable word for word.**
Step 1 (`reports/step1_americanair.md`): questions repeat by meaning (median
closest earlier question 0.90), but the earlier question's answer is barely closer
to the real reply than a random earlier answer: 0.41 against 0.39 character
similarity, and a near copy in 0 to 3% of cases even at the highest question
similarity. The airline writes each reply fresh. This is the spec's first failure
signal, for this brand. Not yet a verdict: character similarity is harsh on
replies that say the same thing in new words, and whether an earlier answer
*would have done* is what the hand labels in step 3 measure. Next: the same check
on Apple Support, whose troubleshooting replies look more templated, before
deciding which brand to build on.

Also seen in step 1: sarcasm defeats retrieval ("Thanks for losing my bag!" sits
at 0.95 next to "Thanks for getting my luggage back to me!"), and the embedding
model's similarities are compressed into 0.85 to 0.95, so thresholds must come
from labels, not intuition.

**Model: `llama-3.3-70b-versatile` is gone from Groq.** Default is now
`openai/gpt-oss-120b`, with `openai/gpt-oss-20b` as the rate-limit fallback.
Provider and model are `.env` settings, not code.

**Commitment rules tightened after running them over all 28,735 real American
Airlines replies.** Under 1% of replies trigger any rule, so the rules do not
over-flag. False positives fixed and kept as regression tests: "they'll issue you
a new boarding pass" (was read as a refund), "we promise our coffee is AAmazing"
(liability), "expedite the boarding process" (escalation), "travel waiver
program" (exception), "we'll send over your praises" (tracked follow-up). A
sample of unflagged risky-looking sentences was nearly all reassurance ("we'll
have you wheels up soon") or feedback forwarding; the softest miss was "we'll
reunite you with your belongings as early as possible".

**Deflection redefined.** A link on its own no longer makes a reply a
deflection: 357 of 400 sampled replies with a link were real answers ("bags can
be up to 50 pounds: [link]"). Deflection now means "DM us", "contact our team",
"give us a call" and similar.

**Redaction widened** after reading real replies: names after "Yes," / "Sure,"
("Yes, Leah, Basic Economy...") are now replaced with `[[name]]`.

**Spec revised after an outside review** (commit `ede7852`):
- mail hygiene (quoted text, signatures, auto-replies, bounces, attachments) moved
  to the later list: the tweet corpus has none of it, so the code could not be
  run against anything real;
- the edit-distance claim restated: the replay shows "more examples help", not
  "human corrections teach it"; the latter needs a small hand-run study;
- one rule file with a direction per rule, instead of "one classifier";
- label about 150 pairs, split roughly 100 to tune and 50 to test;
- the HTML draft wrapper moved to the later list;
- kept against the review's advice, by the owner's decision: spike detection, the
  frequent-complaints view and the action list, as the second half of the pitch
  (actionable extraction from the inbox).

**Development corpus chosen:** Customer Support on Twitter, licence confirmed as
CC BY-NC-SA 4.0 on the Kaggle page. Brands: American Airlines (commitments:
credits, fees, waivers) and Apple Support (repetitive troubleshooting, and the
iOS 11 "I" autocorrect bug inside the data window as a natural spike test).
