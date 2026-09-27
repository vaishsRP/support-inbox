# Replay: Apple Support (Twitter corpus stand-in)

Run `replay-20260927-0333`, 45 customer messages sampled across 3 time slices.
Thresholds: reuse 0.93, docs 0.86, high 0.96, medium 0.94 (**provisional**: set by eye from the step 1 report, not yet from hand labels)

**Read this first.** Similarity here is between the draft and the reply the firm actually sent. It is a
reference comparison, not how much a human edited the draft: nobody edits anything in a replay.

## Where mail went

| route | mails | share |
|---|---:|---:|
| refused | 27 | 60% |
| reuse | 18 | 40% |

Drafted: 40%. Of the messages the firm itself only deflected ("DM us"): 9 of 27 got a draft.

## Does a bigger pool help? (the replay curve)

| slice | pool size (median) | mails | drafted | draft vs actual reply | closest answer reused word for word |
|---:|---:|---:|---:|---:|---:|
| 0 | 4,475 | 15 | 13% | 0.43 | 0.43 |
| 1 | 23,114 | 15 | 40% | 0.70 | 0.70 |
| 2 | 38,965 | 15 | 67% | 0.41 | 0.41 |

## Commitments

Actual replies in this sample that commit beyond authority by the rules: 0.
Drafts that had a commitment replaced by a placeholder: 0.

## Examples

- **Customer:** PLEASE fix the glitch that keeps my phone from turning on by touching the home button & DRAINING battery issue. iPhone 7 Plus
  - **Draft** (low, sim 0.93): We'd be happy to help. Are you experiencing this issue on the latest version of iOS 11.0.2?
  - **Actually sent:** Thanks for reaching out, we want to help! Do you have the most recent version of iOS 11.0.3 downloaded? https://t.co/GDrqU22YpT
- **Customer:** Please help me on it? Is possible to do a downgrade? This is very bad
  - **Draft** (low, sim 0.93): There wouldn't be a way to downgrade, but we'll help with the current version. Tell us more details of what's happening.
  - **Actually sent:** Understood, we can help. We have some support steps we'd like to take in DM. Send us a message, we'll meet you there. https://t.co/GDrqU22YpT
- **Customer:** Who else is having problems typing the letter “i” on an iPhone? cc:
  - **Draft** (medium, sim 0.95): We're here to help! Which type of iPhone do you have and in which applications/features does this occur?
  - **Actually sent:** We'd be more than happy to lend a hand. Send us a DM telling us which iPhone you're using & its iOS version number. https://t.co/GDrqU22YpT
- **Customer:** What is up with this bug can’t type the letter “i” without it doing this.. I️ I️ I️ I️
  - **Draft** (medium, sim 0.96): Here’s what you can do to work around the issue until it’s fixed in a future software update: https://t.co/xXaXeeSRt9
  - **Actually sent:** Here’s what you can do to work around the issue until it’s fixed in a future software update: https://t.co/xXaXeeSRt9
- **Customer:** FIX THE I️ BUTTON
  - **Draft** (high, sim 0.97): Here’s what you can do to work around the issue until it’s fixed in a future software update: https://t.co/xXaXeeSRt9
  - **Actually sent:** Here’s what you can do to work around the issue until it’s fixed in a future software update: https://t.co/xXaXeeSRt9
- **Customer:** my phone is frozen and won't even turn off...fix it
  - **Draft** (medium, sim 0.95): We're here for you. Have you tried restarting, or force restarting, your iPhone? Check out this link: https://t.co/8MvHMFZ83h
  - **Actually sent:** We're glad to hear that it's been resolved! If you encounter any other issues, feel free to DM us: https://t.co/GDrqU22YpT
- **Refused:** I see that I’m not the only one who noticed their iPhone battery draining faster & certain features working slower since updating. 🖕 .
  - nothing close enough: best past answer 0.91 (needs 0.93), best document 0.00 (needs 0.86)
- **Refused:** Thanks for ur quick response. My friend is from India Mumbai
  - nothing close enough: best past answer 0.88 (needs 0.93), best document 0.00 (needs 0.86)
- **Refused:** 🔴 Urgent iPhone 6 Plus Collapse the Notes app on the new update IOS 11.0.2 .
  - nothing close enough: best past answer 0.92 (needs 0.93), best document 0.00 (needs 0.86)
- **Refused:** I bought it through t-mobile. The associate said I should have it in 2 weeks. This was 09/25/17. Order says won't ship until 10/30/17. 😡
  - nothing close enough: best past answer 0.89 (needs 0.93), best document 0.00 (needs 0.86)

Raw rows: `data/applesupport/replay-20260927-0333.csv` (not committed: contains corpus text).

```
{
 "mails": 45,
 "drafted": 18,
 "refused": 27,
 "blocked": 0,
 "failed": 0,
 "draft_rate": 0.4,
 "edit_similarity_median": null,
 "edit_similarity_p25": null,
 "edit_similarity_p75": null,
 "outcomes": {
  "pending": 45
 },
 "commitments_tracked": 0,
 "placeholders_leaked": 0
}
```