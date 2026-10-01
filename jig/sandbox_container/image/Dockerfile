# Jig sandbox image: Python, Playwright and Chromium, run as an unprivileged user.
# Build with:  jig sandbox build
# The runtime starts it with a read-only root filesystem, no capabilities, no-new-privileges,
# CPU/memory/pids limits, only the agent's workspace mounted, and no direct network egress.
FROM mcr.microsoft.com/playwright/python:v1.63.0-noble

# The image ships the browsers (in /ms-playwright) but not the Python package; pin it to the same release.
RUN python3 -m pip install --no-cache-dir --break-system-packages playwright==1.63.0

RUN useradd --uid 10001 --user-group --create-home --home-dir /home/jig --shell /bin/bash jig \
    && mkdir -p /workspace /opt/jig \
    && chown jig:jig /workspace

COPY relay.py browser_server.py /opt/jig/
RUN chmod 0444 /opt/jig/*.py && python3 -c "import playwright.sync_api"

ENV HOME=/home/jig \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

USER 10001:10001
WORKDIR /workspace
CMD ["sleep", "infinity"]
