# matrix-nio 0.26 uses vodozemac instead of libolm, so no C crypto library is
# needed here — a plain slim image is enough for full E2EE support.
FROM python:3.13-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Dependencies first, so code changes do not invalidate the layer.
COPY pyproject.toml README.md ./
COPY src/ ./src/
# The [pii] extra (spaCy) and its German model are installed unconditionally —
# BSBOT_PII__ENABLED is a runtime toggle (see shared/config.py), and this image is
# rebuilt fresh on every deploy, so there is no separate "build with PII
# support" step to remember: flip the env var, no rebuild needed.
RUN pip install --no-cache-dir ".[pii]" \
 && python -m spacy download de_core_news_md

# Runs as a non-root user; /data is the only writable path it needs.
RUN useradd --create-home --uid 10001 bsbot \
 && mkdir -p /data && chown -R bsbot:bsbot /data /app
USER bsbot

ENV BSBOT_DATA_DIR=/data
VOLUME ["/data"]

ENTRYPOINT ["bsbot"]
CMD ["serve"]
