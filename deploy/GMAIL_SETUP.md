# Connecting a Gmail account

About 20 minutes, free. Use a separate Gmail account made for the demo, never your
personal one: the tool reads everything in the inbox it is connected to.

## 1. Create the Google Cloud project (once)

1. Go to console.cloud.google.com and sign in **with the demo Gmail account**.
2. Top bar → project picker → **New project** → name it `support-inbox` → Create.
3. Menu → **APIs & Services → Library** → search **Gmail API** → **Enable**.
4. Menu → **Google Auth Platform** (older consoles: *OAuth consent screen*):
   - **Branding**: app name `Support inbox`, your email as support and developer contact.
   - **Audience**: user type **External**, publishing status **Testing**. Under *Test
     users*, add the demo Gmail address.
   - **Data access**: add the scope `.../auth/gmail.modify` (read, label, manage drafts).
5. **Clients → Create client** → application type **Desktop app** → Create →
   **Download JSON**. Save it as `data/credentials.json` in this project (the `data/`
   folder is git-ignored, so it never reaches GitHub).

## 2. Sign in (once a week in testing mode)

```bash
python -m inbox --firm demo gmail-auth
```

A browser opens. Choose the demo account. Google warns that the app is unverified:
**Advanced → Go to Support inbox (unsafe)** is expected for your own testing app.
Allow access. The token is saved in `data/demo/gmail_token.json`.

In testing mode Google expires the login after about seven days; run `gmail-auth`
again when the watcher says so.

## 3. Run it

```bash
python -m inbox --firm demo gmail-run
```

It starts from now (older mail is left alone) and checks every 90 seconds. From another
account, email the demo address, for example "When do I get my deposit back?". Within
a couple of minutes the thread has a draft reply and `AI/*` labels. Edit it, press send
in Gmail yourself, and on the next pass the tool records what you changed, puts any
promise on the action list, and adds your reply to what it drafts from.

To see the action list and context page at the same time:

```bash
python -m inbox.app --firm demo      # http://127.0.0.1:8000
```

## Working with it inside Gmail

Every customer email gets a draft in its thread and exactly one label, so every email is
worked the same way: open it, read the draft, do what the notes in [[double brackets]]
say, delete them, press Send. Nothing is ever sent without a person.

| Label | What the draft contains | What you do |
|---|---|---|
| `AI/draft-ready/high`, `/medium`, `/low` | A reply built from the team's earlier replies or the policy pages | Check, fill any `[[...]]`, send |
| `AI/needs-approval` | Either a reply with a red `[[NEEDS APPROVAL: ...]]` in place of a promise, or, for legal threats, data requests and the like, an ESCALATE note plus a safe holding reply | Get the approval or pass it on, then send |
| `AI/no-answer` | A frame: greeting, sign-off, and `[[WRITE YOUR ANSWER: ...]]` with questions you could ask | Write the answer yourself |
| `AI/skipped` | No draft. The email came from a robot (auto-reply, newsletter, bounce) | Nothing |
| `AI/context` | No draft. It was a note you emailed to the assistant | Nothing |

Two drafts in the Drafts folder are pages you edit, not emails; nobody sends them:

- **To-do list.** Promises with deadlines, things
  to look up, spikes. Delete a line when it is done. Type a new line at the bottom to add a
  to-do.
- **Context and policies.** The dated notes and policy
  pages the assistant drafts from. Delete a note's block to remove it (a false alarm, an
  outage that is over). Type a note at the bottom: first line the title (add "for 3 days"
  to set how long), then the details. To replace a policy page, email this address from
  itself with the subject `Policy: <same title>` and the new text.

Edits to those two drafts are read once the draft has stayed unchanged for a whole check
(about 30 to 90 seconds), so nothing half-typed is picked up.

Onboarding an existing mailbox: `python -m inbox --firm demo gmail-import --months 12`
reads the conversations the team already replied to and turns them into the knowledge
base, with dates (newest answer wins, changed policies are flagged), per-customer history
and the team's tone.

## What it can and cannot do in your account

- It can read mail, create and update drafts, and add labels.
- It cannot send. There is no code for it, and a test fails the build if any appears.
  Google's permission technically allows sending, which is why this is enforced in the
  code rather than trusted to the permission.
- It never edits a draft you have started changing.
- It skips auto-replies, bounces and newsletters, and threads you already answered.
