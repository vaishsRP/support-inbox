FROM python:3.12-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HF_HOME=/app/data/hf

# CPU-only PyTorch keeps the image a fraction of the default size.
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu

COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir ".[embed]"
COPY config ./config

# data/ (database, vectors, model cache) and .env are mounted at run time, never baked in.
VOLUME ["/app/data"]
EXPOSE 8000

# Inside the container it must listen on all interfaces; publish it on localhost only:
#   docker run --env-file .env -v "$PWD/data:/app/data" -p 127.0.0.1:8000:8000 support-inbox
CMD ["python", "-m", "inbox.app", "--host", "0.0.0.0", "--port", "8000"]
