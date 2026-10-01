# syntax=docker/dockerfile:1.7
# Jig image: the runtime, API, web UI and avatar. Multi-stage, slim, non-root, pinned.
#   docker compose build            (or: docker build -t ghcr.io/rlesueur/jig:dev .)
# Configuration comes from /etc/jig/jig.toml (deploy/jig.toml, mounted by compose.yaml) and JIG_*
# environment variables. State lives in /var/lib/jig/data (a named volume). See docs/container.md.

ARG PYTHON_IMAGE=python:3.13.15-slim-bookworm@sha256:2325bb286ec344af3e5898cc224b5844e2707ac6e26b1632516fd3edc84a5e26

# Dependencies: hash-pinned wheels only (no compilers, no sdists), installed into a virtualenv.
FROM ${PYTHON_IMAGE} AS deps
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
COPY deploy/requirements.lock /tmp/requirements.lock
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --require-hashes --only-binary=:all: --no-deps -r /tmp/requirements.lock \
    && /opt/venv/bin/pip uninstall -y pip \
    && find /opt/venv -name '__pycache__' -prune -exec rm -rf {} +

FROM ${PYTHON_IMAGE}
ARG VERSION=0.1.0
ARG REVISION=unknown
LABEL org.opencontainers.image.title="Jig" \
      org.opencontainers.image.description="Open-source, always-on personal AI agent that runs on your own local model" \
      org.opencontainers.image.source="https://github.com/rlesueur/jig" \
      org.opencontainers.image.url="https://github.com/rlesueur/jig" \
      org.opencontainers.image.documentation="https://github.com/rlesueur/jig/blob/main/docs/container.md" \
      org.opencontainers.image.licenses="Apache-2.0" \
      org.opencontainers.image.authors="Robyn Le Sueur" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${REVISION}"

RUN groupadd --gid 10001 jig \
    && useradd --uid 10001 --gid 10001 --no-create-home --home-dir /var/lib/jig --shell /usr/sbin/nologin jig \
    && mkdir -p /var/lib/jig/data /var/lib/jig/sandbox/default /etc/jig \
    && chown -R 10001:10001 /var/lib/jig \
    && printf '#!/bin/sh\nexec python -m jig.cli "$@"\n' > /usr/local/bin/jig \
    && chmod 0755 /usr/local/bin/jig

COPY --from=deps /opt/venv /opt/venv
COPY LICENSE /opt/jig/LICENSE
COPY avatar/jig-avatar.js /opt/jig/avatar/jig-avatar.js
COPY jig /opt/jig/jig
COPY deploy/jig.toml /etc/jig/jig.toml

ENV PATH=/opt/venv/bin:$PATH \
    PYTHONPATH=/opt/jig \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    JIG_CONFIG=/etc/jig/jig.toml \
    HOME=/var/lib/jig

# Byte-compile once at build time; the container's root filesystem is read-only at run time.
RUN find /opt/jig -name '__pycache__' -prune -exec rm -rf {} + \
    && python -m compileall -q /opt/jig/jig /opt/venv/lib \
    && python -c "import jig.cli, jig.api, jig.sandbox_compose, jig.vault_backends.keyfile"

USER 10001:10001
WORKDIR /var/lib/jig
EXPOSE 8766
# /health is public and returns only {"status": "ok"}; the start period covers a model that is still loading.
HEALTHCHECK --interval=30s --timeout=5s --start-period=15m --retries=3 \
    CMD ["python", "-c", "import sys, urllib.request; sys.exit(urllib.request.urlopen('http://127.0.0.1:8766/health', timeout=4).status != 200)"]
CMD ["jig", "serve"]
