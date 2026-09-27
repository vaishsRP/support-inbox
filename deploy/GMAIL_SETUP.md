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

- **Labels.** Each email gets exactly one: `AI/draft-ready/high`, `/medium` or `/low` (a
  draft is waiting in the thread), `AI/needs-approval` (a lead must decide, or it was not
  drafted at all, like a legal threat), `AI/no-answer` (nothing to draft from) or
  `AI/automated` (auto-replies and newsletters, left alone).
- **The action list** is a draft in your Drafts folder called "Action list (kept up to
  date by the support assistant)". It lists promises with their deadlines, things to look
  up and spikes. Delete a line when it is done; the next check marks it done and rewrites
  the list. Nobody needs to send it.
- **Adding context.** Email the support address from itself with a subject starting
  `Note:` (for example `Note: Heating outage Block C for 3 days`). The body becomes a dated
  note the next drafts use, gone after 14 days or the days you give. `Policy:` adds a
  permanent page instead. Only the support address itself can do this.
- **Onboarding an existing mailbox.** `python -m inbox --firm demo gmail-import --months 12`
  reads the conversations the team already replied to and turns them into the knowledge
  base, with dates, per-customer history and the team's tone.

## What it can and cannot do in your account

- It can read mail, create and update drafts, and add labels.
- It cannot send. There is no code for it, and a test fails the build if any appears.
  Google's permission technically allows sending, which is why this is enforced in the
  code rather than trusted to the permission.
- It never edits a draft you have started changing.
- It skips auto-replies, bounces and newsletters, and threads you already answered.
