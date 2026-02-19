# Cost Explorer MCP Server

MCP server for analyzing AWS costs and usage data through the AWS Cost Explorer API, designed for **Kubernetes deployments using IAM Roles Anywhere** and **multi-client (multi-account) role assumption**.

## Table of Contents

- [Features](#features)
- [Authentication Architecture](#authentication-architecture)
- [Configuration](#configuration)
- [Validation and Cost Optimization](#validation-and-cost-optimization)
- [Available Tools](#available-tools)
- [Deployment](#deployment)
- [Testing](#testing)
- [Usage Examples](#usage-examples)
- [License](#license)

---

## Features

### Analyze AWS costs and usage
- Detailed breakdown of costs by service, region, and other dimensions
- Query historical cost data for specific time periods
- Filter costs by dimensions, tags, and cost categories

### Compare costs between time periods
- Leverage AWS Cost Explorer's [Cost Comparison feature](https://docs.aws.amazon.com/cost-management/latest/userguide/ce-cost-comparison.html)
- Compare costs between two time periods to identify changes and trends
- Analyze cost drivers (top 10) to understand what caused increases/decreases

### Forecast future costs
- Generate cost forecasts based on historical usage patterns
- Predictions with confidence intervals (80% or 95%)
- Daily and monthly forecast granularity

### Multi-client architecture
- Support for multiple AWS accounts with different IAM roles
- Thread-safe session management with LRU cache (max 1000 sessions)
- Automatic token refresh 5 minutes before expiration
- Concurrent request deduplication (only one STS call per client)

---

## Authentication Architecture

### How it works

This server is designed to run in **Kubernetes**, where the pod obtains **base AWS credentials via IAM Roles Anywhere**, and then **for each request**, the server assumes a client-specific IAM role (cross-account) based on `client_id`.

**Authentication flow:**
1. Pod gets base credentials via IAM Roles Anywhere (no AWS_PROFILE inside the container)
2. You provide a `clients.json` mapping: `client_id -> role_arn`
3. For each tool request:
   - You pass `client_id`
   - The server looks up `role_arn` in `clients.json`
   - The server calls `sts:AssumeRole` into the client role
   - A Cost Explorer (ce) client is created and cached until token expiration
   - Tokens are refreshed automatically before expiration

### Prerequisites

- **IAM Roles Anywhere** configured in your AWS account
- **Kubernetes** with Roles Anywhere credentials mounted (implementation-specific)
- **Base role (runner role)**: the role the pod runs as, with permissions to assume client roles
  - `sts:AssumeRole` on each client role ARN
- **Trust policy** of each client role: allows assumption by the base role (with `ExternalId` if required)

### Concurrency handling

The server handles concurrent requests safely:
- **Thread-safe caches**: All client caches use locks to prevent race conditions
- **Refresh deduplication**: If multiple requests need to refresh the same client token, only one STS call is made
- **LRU eviction**: Maximum 1000 cached sessions with automatic eviction of least recently used

### Required IAM Permissions

**Client role** (the role being assumed):
```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "ce:GetCostAndUsage",
        "ce:GetDimensionValues",
        "ce:GetTags",
        "ce:GetCostForecast",
        "ce:GetCostAndUsageComparisons",
        "ce:GetCostComparisonDrivers"
      ],
      "Resource": "*"
    }
  ]
}
```

**Base role** (the pod's identity):
```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": "sts:AssumeRole",
      "Resource": [
        "arn:aws:iam::111111111111:role/CostExplorerRole",
        "arn:aws:iam::222222222222:role/CostExplorerRole"
      ]
    }
  ]
}
```

**Trust policy** on each client role:
```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "AWS": "arn:aws:iam::BASE_ACCOUNT:role/BaseRole"
      },
      "Action": "sts:AssumeRole"
    }
  ]
}
```

---

## Configuration

### Multi-Client Configuration (clients.json)

Create a `clients.json` file and mount it into the container. The server uses `client_id` to look up the role to assume.

#### Example `clients.json`
```json
{
  "clients": {
    "client-finance": {
      "role_arn": "arn:aws:iam::111111111111:role/team-1-cost-explorer",
      "description": "team 1 access"
    },
    "client-engineering": {
      "role_arn": "arn:aws:iam::222222222222:role/team-2-cost-explorer",
      "description": "team 2 access"
    }
  }
}
```

### Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `CLIENTS_CONFIG_PATH` | Yes | - | Path to `clients.json` inside the container |
| `AWS_REGION` | No | `us-east-1` | Default AWS region |
| `FASTMCP_LOG_LEVEL` | No | `WARNING` | Logging level (ERROR, WARNING, INFO, DEBUG) |
| `VALIDATE_FILTER_VALUES` | No | `false` | Enable AWS API calls for filter validation (see below) |

---

## Available Tools

The Cost Explorer MCP Server provides the following tools:

### Utilities
| Tool | Description |
|------|-----------|
| `get_today_date` | Get the current date and month to determine relevant data |
| `list_active_sessions` | List all active Cost Explorer client sessions (for monitoring multiple concurrent clients) |
| `close_session` | Close a specific client session and free up resources |

### Cost Queries
| Tool | Description |
|------|-----------|
| `get_dimension_values` | Get available values for a specific dimension (e.g., SERVICE, REGION) |
| `get_tag_values` | Get available values for a specific tag key |
| `get_cost_and_usage` | Retrieve AWS cost and usage data with filtering and grouping options |

### Analysis and Comparison
| Tool | Description |
|------|-----------|
| `get_cost_and_usage_comparisons` | Compare costs between two time periods to identify changes and trends |
| `get_cost_comparison_drivers` | Analyze what drove cost changes between periods (top 10 most significant drivers) |
| `get_cost_forecast` | Generate cost forecasts based on historical usage patterns |

### Multi-Client Usage

**Important**: Always pass `client_id` when calling a tool. The server will assume the corresponding role from `clients.json`.

#### Example: `get_cost_and_usage`
```json
{
  "client_id": "client-finance",
  "date_range": {
    "start_date": "2026-01-01",
    "end_date": "2026-01-31"
  },
  "granularity": "MONTHLY",
  "group_by": "SERVICE",
  "metric": "UnblendedCost"
}
```

---

## Validation and Cost Optimization

### How validation works

Each Cost Explorer API call costs **$0.01**. The server performs validation on requests before sending them to AWS.

**Local validations (always performed, no AWS calls):**
- Date format (YYYY-MM-DD)
- Date range logic (start before end)
- Granularity-specific constraints (e.g., HOURLY max 14 days)
- Dimension key validation (SERVICE, REGION, etc. are valid keys)
- Filter structure validation (And, Or, Not operators)
- Group by validation

**AWS validations (optional, disabled by default):**
- Validate that dimension values exist (e.g., "Amazon EC2" is a valid SERVICE value)
- Validate that tag values exist

### Controlling validation

By default, AWS validations are **disabled** to reduce costs. If a user passes an invalid value, AWS will return an error which the server will relay.

To enable AWS validations:
```bash
export VALIDATE_FILTER_VALUES=true
```

**Recommendation**: Keep disabled (`false`) in production. Invalid values will result in AWS errors, which is acceptable and saves money.

### Cost estimation

| Operation | API Calls | Cost |
|-----------|-----------|------|
| Simple cost query | 1 | $0.01 |
| Cost query with filter (validation disabled) | 1 | $0.01 |
| Cost query with filter (validation enabled) | 2+ | $0.02+ |
| Cost comparison | 2 | $0.02 |
| Cost forecast | 1 | $0.01 |

---

## Deployment

### Docker Build

```bash
docker build -t cost-explorer-mcp:latest .
```

### Docker Run (local testing with AWS credentials)

```bash
docker run -d \
  -e AWS_REGION=us-east-1 \
  -e CLIENTS_CONFIG_PATH=/config/clients.json \
  -e FASTMCP_LOG_LEVEL=INFO \
  -v $(pwd)/clients.json:/config/clients.json:ro \
  -v ~/.aws:/root/.aws:ro \
  cost-explorer-mcp:latest
```

### K3s with IAM Roles Anywhere

For K3s with IAM Roles Anywhere, mount the credentials:

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: cost-explorer-mcp
spec:
  replicas: 1
  selector:
    matchLabels:
      app: cost-explorer-mcp
  template:
    metadata:
      labels:
        app: cost-explorer-mcp
    spec:
      containers:
      - name: cost-explorer
        image: cost-explorer-mcp:latest
        env:
        - name: AWS_REGION
          value: "us-east-1"
        - name: CLIENTS_CONFIG_PATH
          value: "/config/clients.json"
        - name: VALIDATE_FILTER_VALUES
          value: "false"
        volumeMounts:
        - name: clients-config
          mountPath: /config
          readOnly: true
        - name: iam-anywhere-creds
          mountPath: /var/run/aws
          readOnly: true
      volumes:
      - name: clients-config
        configMap:
          name: cost-explorer-clients
      - name: iam-anywhere-creds
        hostPath:
          path: /var/run/aws
          type: Directory
```

### EKS with IRSA

For EKS, use IAM Roles for Service Accounts (IRSA):

```yaml
apiVersion: v1
kind: ServiceAccount
metadata:
  name: cost-explorer-sa
  annotations:
    eks.amazonaws.com/role-arn: arn:aws:iam::ACCOUNT:role/CostExplorerBaseRole
```

---

## Testing

### Running Unit Tests

```bash
# Create virtual environment and install dependencies
uv sync --dev

# Activate the virtual environment
source .venv/bin/activate

# Run all tests (293 tests)
pytest tests/ -v -o "addopts="

# Run auth tests specifically (56 tests, 96% coverage)
pytest tests/test_auth_multiclient.py -v -o "addopts="

# Run with coverage report
pytest tests/ --cov=awslabs/cost_explorer_mcp_server --cov-report=term-missing -o "addopts="
```

### Test Coverage

The test suite includes 293 tests covering all handlers and the multi-client authentication system:

| Module | Tests | Coverage |
|--------|-------|---------|
| `auth.py` (multi-client) | 56 | 96% |
| `cost_usage_handler.py` | 50+ | 95%+ |
| `comparison_handler.py` | 30+ | 95%+ |
| `forecasting_handler.py` | 20+ | 95%+ |
| `metadata_handler.py` | 20+ | 95%+ |
| `utility_handler.py` | 10+ | 100% |
| `server.py` | 10+ | 95%+ |

---

## Usage Examples

Here are some examples of how to use the Cost Explorer MCP Server with natural language queries:

### Cost Analysis Examples

```
Show me my AWS costs for the last 3 months grouped by service in us-east-1 region
Break down my S3 costs by storage class for Q1 2025
Show me costs for production resources tagged with Environment=prod
What were my costs for reserved instances vs on-demand in May?
What was my EC2 instance usage by instance type?
```

### Cost Comparison Examples

```
Compare my AWS costs between April and May 2025
How did my EC2 costs change from last month to this month?
Why did my AWS bill increase in June compared to May?
What caused the spike in my S3 costs last month?
```

### Forecasting Examples

```
Forecast my AWS costs for next month
Predict my EC2 spending for the next quarter
What will my total AWS bill be for the rest of 2025?
```

---

## License

This project is licensed under the Apache License 2.0 - see the LICENSE file for details.
