# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# dependabot should continue to update this to the latest hash.
FROM public.ecr.aws/amazonlinux/amazonlinux@sha256:50a58a006d3381e38160fc5bb4bbefa68b74fcd70dde798f68667aac24312f20 AS uv

# Install build dependencies needed for compiling packages
RUN dnf install -y shadow-utils python3 python3-devel gcc && \
    dnf clean all

# Install the project into `/app`
WORKDIR /app

# Enable bytecode compilation
ENV UV_COMPILE_BYTECODE=1

# Copy from the cache instead of linking since it's a mounted volume
ENV UV_LINK_MODE=copy

# Prefer the system python (critical for multi-stage builds)
ENV UV_PYTHON_PREFERENCE=only-system

# Run without updating the uv.lock file like running with `--frozen`
ENV UV_FROZEN=true

# Copy the required files first
COPY pyproject.toml uv.lock uv-requirements.txt ./

# Python optimization and uv configuration
ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Install Python 3.12 (system python) to be used by uv
RUN dnf install -y python3.12 && dnf clean all

# Install the project's dependencies using the lockfile and settings
RUN --mount=type=cache,target=/root/.cache/uv \
    python3 -m ensurepip && \
    python3 -m pip install --require-hashes --requirement uv-requirements.txt --no-cache-dir && \
    uv sync --python /usr/bin/python3.12 --frozen --no-install-project --no-dev --no-editable

# Then, add the rest of the project source code and install it
# Installing separately from its dependencies allows optimal layer caching
COPY . /app
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --python /usr/bin/python3.12 --frozen --no-dev --no-editable

# Final runtime image
FROM public.ecr.aws/amazonlinux/amazonlinux@sha256:50a58a006d3381e38160fc5bb4bbefa68b74fcd70dde798f68667aac24312f20

# Place executables in the environment at the front of the path
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install Python, procps for healthcheck (pgrep), nc for port check, and create non-root user
RUN dnf install -y shadow-utils procps nmap-ncat python3.12 && \
    dnf clean all && \
    groupadd --force --system app && \
    useradd app -g app -d /app

# Get the project from the uv layer
COPY --from=uv --chown=app:app /app/.venv /app/.venv
COPY --from=uv --chown=app:app /app/awslabs /app/awslabs

# Create config directory for clients.json mount
RUN mkdir -p /config && chown app:app /config

# Get healthcheck script
COPY --chown=app:app ./docker-healthcheck.sh /usr/local/bin/docker-healthcheck.sh
RUN chmod +x /usr/local/bin/docker-healthcheck.sh

# Run as non-root
USER app

# Healthcheck for MCP server process
HEALTHCHECK --interval=60s --timeout=10s --start-period=10s --retries=3 CMD ["docker-healthcheck.sh"]

# Multi-client configuration:
# Mount clients.json to /config/clients.json and set CLIENTS_CONFIG_PATH=/config/clients.json
# 
# Environment variables:
# CLIENTS_CONFIG_PATH: Required - Path to clients.json mapping client_id -> role_arn
# AWS_REGION: Optional (default: us-east-1)
# FASTMCP_LOG_LEVEL: Optional (default: WARNING) - ERROR, WARNING, INFO, DEBUG
# VALIDATE_FILTER_VALUES: Optional (default: false) - Enable $0.01 AWS validation calls
# MCP_TRANSPORT: Optional (default: sse) - Transport mode: stdio, sse, or streamable-http
# MCP_HOST: Optional (default: 0.0.0.0 in container) - Host to bind to
# MCP_PORT: Optional (default: 8000) - Port to listen on
# MCP_MOUNT_PATH: Optional - Mount path for SSE/HTTP transport

# Set defaults for container deployment
ENV MCP_TRANSPORT=sse \
    MCP_HOST=0.0.0.0 \
    MCP_PORT=8000

# Expose port 8000 for SSE/HTTP transport
EXPOSE 8000

ENTRYPOINT ["awslabs.cost-explorer-mcp-server"]
