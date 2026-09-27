# Putting the demo online (free)

The demo runs on a free Hugging Face Space: 2 CPUs, 16 GB of memory, no card needed.
It sleeps after about 48 hours without visitors and takes a minute or two to wake.

1. Make a free account at huggingface.co.
2. New Space → name it `support-inbox-demo` → SDK: **Docker** → template: **Blank** →
   hardware: **CPU basic (free)** → Public → Create.
3. In the Space, **Files → Add file → Upload files**: upload `deploy/hf-space/Dockerfile`
   and `deploy/hf-space/README.md` from this repo. Commit.
4. **Settings → Variables and secrets → New secret**: name `GROQ_API_KEY`, value your key
   from console.groq.com. (A secret, not a variable: secrets are never shown.)
5. The Space builds (about 10 minutes the first time) and starts. Its address is
   `https://<your-username>-support-inbox-demo.hf.space`.
6. Put that link at the top of the GitHub README and on the portfolio site.

To update the demo after pushing to GitHub: Settings → **Factory rebuild**. It clones
the repo again.

What visitors can and cannot do:

- Each visitor has a private session. Nobody sees anyone else's emails or notes.
- 25 emails and 5 notes per session. Replies a visitor sends never teach the demo
  anything, so one visitor cannot plant an answer another visitor then gets.
- It spends the free Groq quota. If the daily free limit runs out, drafts fail with a
  message saying so, and no bill is possible: there is no card on the Groq account.
