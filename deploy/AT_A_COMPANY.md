# Running this for a real support team

What a company actually gets, how it is installed, and what has to be true first.

## What they get

Nothing new to learn for the support team. They keep working in Gmail:

- draft replies appear in the threads, ready to check and send;
- `AI/*` labels sort the inbox: ready, needs approval, no answer;
- a morning digest arrives as a draft for the team lead to send round.

One small web page for the team lead, the **dashboard**: the action list (promises
with deadlines, things to look up, spikes) and the context page (policy pages and
incident notes). It is served by the same program at an internal address, for example
`https://support-assistant.company.internal`.

There is no app to install on anyone's computer and no browser extension.

## How it is installed

The whole thing is one program in one container (the `Dockerfile` in this repo). It
runs somewhere the company controls, next to one small database file:

| Option | Good for | Cost |
|---|---|---|
| A small cloud server the company already has (a 2 CPU, 4 GB virtual machine) | most teams | a few euros a month |
| An office machine that stays on | trying it out | nothing |
| Their existing container platform | companies with an IT team | usually nothing extra |

Steps for their IT person, about an hour:

1. Run the container with the company's config file and a folder for the database.
2. Connect the support mailbox (see below).
3. Put the dashboard behind the company's normal sign-in (Google sign-in through an
   identity-aware proxy, or the VPN). The dashboard has no login of its own yet.
4. Import the mailbox history, upload the policy pages on the context page.
5. The team lead labels about 150 past pairs (an hour) to set the thresholds.

## Connecting their Gmail: the part that decides everything

**If the company uses Google Workspace** (Gmail with their own domain), this is easy:
their Workspace admin creates the connection as an **internal** app. Internal apps are
only usable inside the company, need **no Google verification**, and their logins do
**not** expire weekly. This is the realistic path for a real employer.

**If it is a plain @gmail.com inbox**, the app stays in testing mode (logins expire
weekly) until it passes Google's restricted-scope verification, which includes a
security assessment and takes weeks. Not worth it for one company.

## Before any real customer mail goes through it

These are not optional, and none of them is built into the demo:

1. **Permission.** Customer mail is personal data under the GDPR. The company's privacy
   officer has to agree, and it goes in their record of processing.
2. **The model runs where the mail is.** Groq's free tier is for the demo only. For real
   mail, run the model on the company's own machine with Ollama (one setting in `.env`,
   no code change), or use a paid provider under a data processing agreement that
   excludes training on inputs.
3. **Deleting one customer's data on request**, and a retention period for stored mail.
4. **Sign-in in front of the dashboard** (step 3 above).
5. **Thresholds labelled on their own mail.** The ones in this repo belong to the
   public data they came from.

## If it were your employer

Ask first, show them the demo, and propose a pilot:

- one shared support inbox, Workspace internal app, model running locally;
- two weeks, drafts only (it cannot send anyway), the team keeps working as usual;
- at the end, the three numbers the project measures: how much mail got a draft, how
  much the team changed the drafts before sending, and how many promises were caught.

Those numbers decide whether it is worth keeping, which is a better conversation than
"trust the AI".

## What is not built for this yet

- A login on the dashboard (use the company's proxy or VPN).
- Several mailboxes or several teams in one installation (one config file per team,
  one container each, is the intended way).
- Outlook / Microsoft 365. The Gmail module is the only mailbox connector; a Microsoft
  Graph one would be the same four operations.
