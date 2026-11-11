# The browser tests run on the compose network so that Moodle (http://moodle) and the
# API (http://api:8000, ws://api:8000) have the same URLs for the browser and for each other.
FROM mcr.microsoft.com/playwright/python:v1.64.1-noble
RUN pip install --no-cache-dir "playwright==1.64.1" "pytest>=8.2" "pytest-playwright>=0.10" "pytest-timeout>=2.3" "httpx>=0.27"
WORKDIR /repo
COPY pyproject.toml /repo/pyproject.toml
COPY tests /repo/tests
RUN mkdir -p /artifacts
