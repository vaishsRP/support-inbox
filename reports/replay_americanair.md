# Replay: American Airlines (Twitter corpus stand-in)

Run `replay-20260927-0306`, 60 customer messages sampled across 3 time slices.
Thresholds: reuse 0.93, docs 0.86, high 0.96, medium 0.94 (**provisional**: set by eye from the step 1 report, not yet from hand labels)

**Read this first.** Similarity here is between the draft and the reply the firm actually sent. It is a
reference comparison, not how much a human edited the draft: nobody edits anything in a replay.

## Where mail went

| route | mails | share |
|---|---:|---:|
| refused | 56 | 93% |
| reuse | 4 | 7% |

Drafted: 7%. Of the messages the firm itself only deflected ("DM us"): 1 of 10 got a draft.

## Does a bigger pool help? (the replay curve)

| slice | pool size (median) | mails | drafted | draft vs actual reply | closest answer reused word for word |
|---:|---:|---:|---:|---:|---:|
| 0 | 2,934 | 20 | 5% | 0.43 | 0.43 |
| 1 | 12,478 | 20 | 0% |  |  |
| 2 | 25,333 | 20 | 15% | 0.40 | 0.40 |

## Commitments

Actual replies in this sample that commit beyond authority by the rules: 0.
Drafts that had a commitment replaced by a placeholder: 0.

## Examples

- **Customer:** Great job on the remodeled Admirals Club! Looks great.
  - **Draft** (low, sim 0.93): We do our best to impress and we're so glad you like it! #winning
  - **Actually sent:** We're absolutely thrilled hearing you like the new look! Please relax and enjoy.
- **Customer:** Can someone assist me?
  - **Draft** (high, sim 0.98): We're here to help 24/7. What's going on?
  - **Actually sent:** DM your record locator along with your date of birth and KTN number. We'll check it for you.
- **Customer:** I’ve have flown every major airline there is to fly. has officially provided my worst experience ever.
  - **Draft** (low, sim 0.94): That's not what we like to hear. We hope you'll give us another chance soon [[name]].
  - **Actually sent:** We're so sorry this has been your experience. Please let us know if we can help in some way.
- **Customer:** Seriously, ?? When will I find out if my flight will be affected? What are the alternatives?! Ridiculous 😡 [link]
  - **Draft** (medium, sim 0.94): We know your holiday plans are important and we fully expect to avoid cancellations.
  - **Actually sent:** We aren’t expecting our flights to be affected. Please sign up here for any changes: [link]
- **Refused:** No screen, no power. Hard to understand why this is considered acceptable for a 5hr 45 mins flight.
  - nothing close enough: best past answer 0.89 (needs 0.93), best document 0.00 (needs 0.86)
- **Refused:** You have an awesome #AATeam member at ELP. I left my phone on the plane; Ramie from lost baggage went on the plane & found it!
  - nothing close enough: best past answer 0.89 (needs 0.93), best document 0.00 (needs 0.86)
- **Refused:** what happened to common sense? Can't keep a door open for 30 seconds longer even though YOU fucked up? #boycott #unamerican
  - nothing close enough: best past answer 0.87 (needs 0.93), best document 0.00 (needs 0.86)
- **Refused:** MADE IT!! Just ran thru the airport home alone style I MADE IT TO MY GATE!
  - nothing close enough: best past answer 0.89 (needs 0.93), best document 0.00 (needs 0.86)

Raw rows: `data/americanair/replay-20260927-0306.csv` (not committed: contains corpus text).

```
{
 "mails": 60,
 "drafted": 4,
 "refused": 56,
 "blocked": 0,
 "failed": 0,
 "draft_rate": 0.06666666666666667,
 "edit_similarity_median": null,
 "edit_similarity_p25": null,
 "edit_similarity_p75": null,
 "outcomes": {
  "pending": 60
 },
 "commitments_tracked": 0,
 "placeholders_leaked": 0
}
```