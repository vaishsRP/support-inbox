# Step 1: how repetitive is American Airlines (Twitter corpus stand-in)?

Generated 2026-09-27 02:28 UTC by `python -m inbox --firm americanair explore`.

Corpus: 36,420 customer message / firm reply pairs, asked 2011-06-16 to 2017-12-03.
Deflections ("DM us", "contact our team"): 7,685 (21%). They are
excluded below unless marked, because they repeat endlessly but answer nothing.

Method: for each question, only questions asked earlier are searchable. Take the
closest by meaning (embedding cosine, `multilingual-e5-small`). Then compare that
earlier question's approved answer with the answer the firm actually sent:
character similarity (1 minus normalised edit distance) and meaning similarity.
The baseline is a random earlier answer. A "near copy" is character similarity of
0.8 or more, meaning a light edit would have turned the reused answer into the
real one.

## Headline

| set | questions | median closest-question sim | share >=0.92 | reused char sim | random char sim | reused meaning sim | random meaning sim | reused near copy | random near copy |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| real answers, all messages | 28,734 | 0.904 | 17% | 0.41 | 0.39 | 0.853 | 0.831 | 0% | 0% |
| real answers, first messages only | 19,572 | 0.905 | 18% | 0.41 | 0.39 | 0.856 | 0.832 | 0% | 0% |
| including deflections (for contrast) | 36,419 | 0.905 | 19% | 0.41 | 0.39 | 0.853 | 0.834 | 0% | 0% |

## Does a closer question mean a more reusable answer?

Real answers only. If reuse works, the right-hand columns rise going down the table.

| closest earlier question similarity | questions | share | reused answer, char similarity | reused answer, meaning similarity | reused answer is a near copy (>=0.8) |
|---|---:|---:|---:|---:|---:|
| <0.80 | 1 | 0% | 0.34 | 0.809 | 0% |
| 0.80-0.85 | 123 | 0% | 0.39 | 0.837 | 0% |
| 0.85-0.88 | 2,428 | 8% | 0.39 | 0.842 | 0% |
| 0.88-0.90 | 9,317 | 32% | 0.40 | 0.848 | 0% |
| 0.90-0.92 | 12,082 | 42% | 0.41 | 0.855 | 0% |
| 0.92-0.94 | 3,709 | 13% | 0.42 | 0.861 | 1% |
| 0.94-0.96 | 625 | 2% | 0.41 | 0.864 | 1% |
| >=0.96 | 449 | 2% | 0.41 | 0.868 | 3% |

## Examples: very close earlier question (first messages only, sim >= 0.94)

- **Q** AA1646, 11-Nov: PHX to LAS -Outstanding, professional service from 1st Class cabin FA Trish - pass on my compliments! #AATeam
  - **Earlier Q** (sim 0.97): AA319, 01-Nov: PHX to PDX -Outstanding, professional service from 1st Class cabin FAs - pass on my compliments! #AATeam
  - **Earlier answer, reused**: This is music to our ears and we'll definitely pass on your compliments. Thanks so much for taking the time to recognize our #AATeam!
  - **Actual answer**: We love that Trish took exceptional care of you, [[name]]. We'll be glad to share your wonderful kudos with her leaders.
  - char similarity 0.37
- **Q** Omg I’m never flying ever again. Seriously worst flying experience I’ve ever encountered and I fly often. 😑
  - **Earlier Q** (sim 0.95): I'm never ever flying again. Literally EVERY bad experience I've had flying has been with them. STAY AWAY 🚫🚫🚫
  - **Earlier answer, reused**: We want your experience to be great every time. Please let us know if we can help.
  - **Actual answer**: Our team is always happy to provide excellent service and experience, what's got you feeling otherwise?
  - char similarity 0.43
- **Q** Thanks for losing my bag!
  - **Earlier Q** (sim 0.95): Thanks for getting my luggage back to me!
  - **Earlier answer, reused**: We're so happy you've been reunited!
  - **Actual answer**: Please ensure you speak with our team before leaving the airport to set up a report: [link]
  - char similarity 0.33
- **Q** God bless 🙌🏻🙏🏻🙌🏻 [link]
  - **Earlier Q** (sim 0.94): Awesome 🙌💪👋👋👋 [link]
  - **Earlier answer, reused**: We're happy to help where we can!
  - **Actual answer**: Welcome to the Gold family, [[name]]! We appreciate you spending all that time in the air with us.
  - char similarity 0.31
