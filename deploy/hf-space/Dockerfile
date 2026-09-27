# Hugging Face Space for the public demo. Builds from the public GitHub repo, so the
# Space itself only holds this file and README.md.
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*
RUN useradd -m -u 1000 user
USER user
ENV HOME=/home/user PATH=/home/user/.local/bin:$PATH HF_HOME=/home/user/.cache/huggingface \
    PYTHONUNBUFFERED=1 INBOX_DEMO=1 INBOX_FIRM=demo PORT=7860
WORKDIR /home/user

RUN pip install --no-cache-dir --user torch --index-url https://download.pytorch.org/whl/cpu
# Bump this line to force a fresh clone of the repo on the next build.
ARG REPO_REF=main
RUN git clone --depth 1 --branch ${REPO_REF} https://github.com/vaishsRP/support-inbox.git app
WORKDIR /home/user/app
RUN pip install --no-cache-dir --user ".[embed]"

# Load the made-up company's past emails and policy pages, and embed them, at build time
# so the first visitor does not wait for it.
RUN python -m inbox --firm demo import && \
    python -m inbox --firm demo retrieve "when do I get my deposit back" > /dev/null

EXPOSE 7860
# GROQ_API_KEY comes from the Space's secret settings, never from the repo.
CMD ["python", "-m", "inbox.app", "--host", "0.0.0.0", "--port", "7860", "--demo", "--firm", "demo"]
