# =============================================================================
# Backend image: FastAPI + the Pi Coding Agent.
#
# Pi is a Node program, so Node is installed alongside Python rather than in a
# separate sidecar container. That keeps the Compose topology to one application
# service and makes the agent a subprocess of the API over JSONL stdio, with no
# network hop and no RPC-over-HTTP translation layer.
# =============================================================================

FROM node:22-slim AS node

# -----------------------------------------------------------------------------
FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates git \
    && rm -rf /var/lib/apt/lists/*

# Node runtime for the Pi Coding Agent (the agent loop).
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -sf /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -sf /usr/local/lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx \
    && node --version

# Pi is resolved from PATH by the Python process pool (PI_BIN).
RUN npm install -g @earendil-works/pi-coding-agent && pi --version

WORKDIR /app

COPY pyproject.toml ./
COPY app ./app
RUN pip install --upgrade pip && pip install ".[dev]"

COPY scripts ./scripts
COPY tests ./tests

# Pi must not inherit host configuration, so it reads a fixed, container-local
# catalog mounted from ./agent at runtime. That mount is read-only, but Pi writes
# its credential store and session dir into its config dir, so the config dir is
# a writable location that the app populates from PI_CONFIG_SOURCE at spawn.
ENV PI_CODING_AGENT_DIR=/tmp/pi-agent \
    PI_CONFIG_SOURCE=/app/agent \
    PI_OFFLINE=1 \
    PI_TELEMETRY=0

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
