# Multi-stage build for Cost Optimizer MCP Server

FROM python:3.12-slim AS builder

WORKDIR /app

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends gcc && \
    rm -rf /var/lib/apt/lists/*

# Copy dependency files first for layer caching
COPY pyproject.toml requirements.txt ./

# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code and install package
COPY cost_optimizer/ cost_optimizer/
RUN pip install --no-cache-dir .

# Final runtime image
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Create non-root user
RUN groupadd --system app && useradd app -g app -d /app

# Copy installed packages from builder
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin/cost-optimizer /usr/local/bin/cost-optimizer

# Install nc for healthcheck port check
RUN apt-get update && apt-get install -y --no-install-recommends procps netcat-openbsd && \
    rm -rf /var/lib/apt/lists/*

# Create config directory for clients.json mount
RUN mkdir -p /config && chown app:app /config

# Get healthcheck script
COPY --chown=app:app ./docker-healthcheck.sh /usr/local/bin/docker-healthcheck.sh
RUN chmod +x /usr/local/bin/docker-healthcheck.sh

# Run as non-root
USER app

# Healthcheck
HEALTHCHECK --interval=60s --timeout=10s --start-period=10s --retries=3 CMD ["docker-healthcheck.sh"]

# Environment variables:
# CLIENTS_CONFIG_PATH: Required - Path to clients.json mapping client_id -> role_arn
# AWS_REGION: Optional (default: us-east-1)
# FASTMCP_LOG_LEVEL: Optional (default: WARNING)
# MCP_TRANSPORT: Optional (default: stdio) - Transport mode: stdio, sse, or streamable-http
# MCP_HOST: Optional (default: 0.0.0.0 in container)
# MCP_PORT: Optional (default: 8000)

ENV MCP_TRANSPORT=streamable-http \
    MCP_HOST=0.0.0.0 \
    MCP_PORT=8000

EXPOSE 8000

ENTRYPOINT ["cost-optimizer"]
