# Step 1: how repetitive is Apple Support (Twitter corpus stand-in)?

Generated 2026-09-27 03:11 UTC by `python -m inbox --firm applesupport explore`.

Corpus: 106,400 customer message / firm reply pairs, asked 2016-03-04 to 2017-12-03.
Deflections ("DM us", "contact our team"): 59,608 (56%). They are
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
| real answers, all messages | 46,791 | 0.927 | 64% | 0.48 | 0.41 | 0.882 | 0.854 | 10% | 2% |
| real answers, first messages only | 33,458 | 0.929 | 69% | 0.50 | 0.42 | 0.889 | 0.856 | 12% | 2% |
| including deflections | skipped for time on this brand | | | | | | | | |

## Does a closer question mean a more reusable answer?

Real answers only. If reuse works, the right-hand columns rise going down the table.

| closest earlier question similarity | questions | share | reused answer, char similarity | reused answer, meaning similarity | reused answer is a near copy (>=0.8) |
|---|---:|---:|---:|---:|---:|
| <0.80 | 1 | 0% | 0.42 | 0.833 | 0% |
| 0.80-0.85 | 47 | 0% | 0.42 | 0.850 | 4% |
| 0.85-0.88 | 753 | 2% | 0.42 | 0.856 | 3% |
| 0.88-0.90 | 3,801 | 8% | 0.44 | 0.863 | 4% |
| 0.90-0.92 | 12,425 | 27% | 0.45 | 0.874 | 7% |
| 0.92-0.94 | 17,044 | 36% | 0.48 | 0.885 | 10% |
| 0.94-0.96 | 8,494 | 18% | 0.51 | 0.896 | 15% |
| >=0.96 | 4,226 | 9% | 0.51 | 0.892 | 17% |

## Examples: very close earlier question (first messages only, sim >= 0.94)

- **Q** why does this happen when I️ am typing anything! Fix this bug fast and why does iOS 11 keep freezing my phone! [link]
  - **Earlier Q** (sim 0.95): my phone is so buggy from the new iOS update. Why does this happen when I️ type an I️? [link]
  - **Earlier answer, reused**: Thank you for contacting us for support. We'd like to help. Let us know if this article helps: [link]
  - **Actual answer**: Here’s what you can do to work around the issue until it’s fixed in a future software update: [link]
  - char similarity 0.47
- **Q** I’m experiencing lag issues when typing in Messages on my iPhone 8 Plus on 11.1.2. Every time I come to the end of a line and it transitions to the next one, it lags.
  - **Earlier Q** (sim 0.95): I updated to ios 11.1 but I’m experiencing a lag when I type in messages. ☹️
  - **Earlier answer, reused**: We're here to help. Can you tell us do you have the same issue after restarting your iPhone?
  - **Actual answer**: We'd love to assist you with this. Have you tried restarting your phone to see if the issue continues? If not, let's follow the steps here: [link]
  - char similarity 0.45
- **Q** Hey Fix I.T already.
  - **Earlier Q** (sim 0.94): Hey fix the I️ issue now.
  - **Earlier answer, reused**: We recently released an iOS update, 11.1.1, that contains a fix for autocorrect issues. Let’s be sure to back up your device prior to updating. How to back up: [link]
  - **Actual answer**: Thanks for reaching out. Which device and OS are you having this issue on? Let us know, we'd like to help.
  - char similarity 0.38
- **Q** every time I get s notification of Snapchat my music stops it happens frequently and is very annoying please fix it
  - **Earlier Q** (sim 0.95): Ugh it’s so fucking annoying that my music stops playing every god damn time I get a stupid Snapchat notification or anything wtf what should I do to make it stop😐. My notification
  - **Earlier answer, reused**: We'd love to help. Are you using the Apple Music app? Do you know what version of the iOS is on the device? If not, you can check under Settings > General > About > Version.
  - **Actual answer**: Let's take a look at what's causing this. To begin, which device and iOS version are you using? Also, do you see a Snapchat update in App Store > Updates? [link]
  - char similarity 0.44
