FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install uv via pip (Debian-based slim image keeps psycopg-binary compatible).
RUN python -m pip install --no-cache-dir uv==0.6.10

# -------------------- Dependency install (cached layer) --------------------
# Copy only the lockfiles first so dependency installs cache well.
COPY backend/pyproject.toml backend/uv.lock ./backend/
WORKDIR /app/backend
RUN uv sync --frozen --no-install-project

# --------------------------- Runtime sources ------------------------------
WORKDIR /app
COPY backend/src/ ./backend/src/
COPY backend/alembic/ ./backend/alembic/
COPY backend/alembic.ini ./backend/alembic.ini

# langgraph dev expects the config at repo root.
COPY langgraph.json ./langgraph.json

# Some tooling expects repo-root structure; data can be mounted at runtime.
COPY infra/ ./infra/

# Install the local project (keeps lockfile frozen, uses src/ already copied).
WORKDIR /app/backend
RUN uv sync --frozen

# Ensure venv executables are on PATH (python, uv, etc.).
ENV VIRTUAL_ENV=/app/backend/.venv
ENV PATH="${VIRTUAL_ENV}/bin:${PATH}"

# Optional: drop root for runtime.
RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin appuser \
    && chown -R appuser:appuser /app
USER appuser
