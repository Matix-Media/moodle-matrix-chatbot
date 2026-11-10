FROM python:3.13-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src/ ./src/
RUN pip install --no-cache-dir .

# Runs as a non-root user; /data is the only writable path it needs.
RUN useradd --create-home --uid 10001 mlc \
 && mkdir -p /data && chown -R mlc:mlc /data /app
USER mlc

ENV MLC_DATA_DIR=/data
VOLUME ["/data"]

ENTRYPOINT ["moodle-llm-chat"]
CMD ["serve"]
