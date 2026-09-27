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

## What it can and cannot do in your account

- It can read mail, create and update drafts, and add labels.
- It cannot send. There is no code for it, and a test fails the build if any appears.
  Google's permission technically allows sending, which is why this is enforced in the
  code rather than trusted to the permission.
- It never edits a draft you have started changing.
- It skips auto-replies, bounces and newsletters, and threads you already answered.
