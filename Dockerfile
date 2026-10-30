# Paper-only dividend agent — multi-stage image (Python 3.12).
FROM python:3.12-slim-bookworm AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy

COPY --from=ghcr.io/astral-sh/uv:0.6.14 /uv /usr/local/bin/uv

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN uv venv /opt/venv && \
    . /opt/venv/bin/activate && \
    uv pip install --no-cache .

FROM python:3.12-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    TZ=America/Denver \
    TRADING_PAUSED=true

RUN groupadd --system --gid 10001 agent && \
    useradd --system --uid 10001 --gid agent --home /nonexistent --shell /usr/sbin/nologin agent && \
    mkdir -p /data /reports /logs /config && \
    chown -R agent:agent /data /reports /logs

COPY --from=builder /opt/venv /opt/venv
COPY --chown=agent:agent config /app/config
COPY --chown=agent:agent src /app/src

WORKDIR /app
USER agent

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD dividend-agent health || exit 1

# Default: validate example config and report health (dry-run safe; no orders).
CMD ["sh", "-c", "dividend-agent config validate /config/strategy.yaml || dividend-agent config validate /app/config/strategy.example.yaml; dividend-agent health; exec sleep infinity"]
