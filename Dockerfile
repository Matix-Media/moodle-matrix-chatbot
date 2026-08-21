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
RUN pip install --no-cache-dir .

# Runs as a non-root user; /data is the only writable path it needs.
RUN useradd --create-home --uid 10001 bsbot \
 && mkdir -p /data && chown -R bsbot:bsbot /data /app
USER bsbot

ENV BSBOT_DATA_DIR=/data
VOLUME ["/data"]

ENTRYPOINT ["bsbot"]
CMD ["serve"]
