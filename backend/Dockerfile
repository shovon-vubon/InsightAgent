# syntax=docker/dockerfile:1

# --- build stage -------------------------------------------------------------
# Dependencies are installed into /opt/venv rather than /app/.venv so that bind
# mounting the source over /app during development does not shadow the venv.
FROM python:3.12-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON=/usr/local/bin/python3.12 \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv

WORKDIR /build
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# --- runtime stage -----------------------------------------------------------
FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH"

RUN groupadd --system app && useradd --system --gid app --home-dir /app app

# The upload store must exist *in the image*, owned by the runtime user. Docker
# seeds a fresh named volume from the image's directory — including its
# ownership — so without this the mount point arrives owned by root and the
# non-root process cannot write to it. Creating it at runtime does not help: the
# volume is mounted over whatever the process makes.
RUN mkdir -p /var/lib/insightagent/uploads \
    && chown -R app:app /var/lib/insightagent

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app
COPY --chown=app:app . .

USER app
EXPOSE 8000

# Uses the stdlib rather than adding curl to the image for the sake of a probe.
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request as r,sys; sys.exit(0 if r.urlopen('http://127.0.0.1:8000/api/v1/health/live', timeout=4).status == 200 else 1)"]

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
