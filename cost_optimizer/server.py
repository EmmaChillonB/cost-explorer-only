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

"""Cost Optimizer MCP server implementation.

This server provides tools for analyzing AWS costs, usage, utilization and savings
through the AWS Cost Explorer, CloudWatch and related APIs.
"""

import os
import sys
from cost_optimizer.cost_explorer import (
    get_cost_and_usage,
    get_cost_and_usage_comparisons,
    get_cost_comparison_drivers,
    get_cost_forecast,
    get_dimension_values,
    get_tag_values,
    get_today_date,
    get_cost_trend_with_anomalies,
)
from cost_optimizer.cost_explorer.savings import get_savings_commitments
from cost_optimizer.inventory import (
    describe_ec2_instances,
    list_ec2_regions_with_instances,
    describe_ebs_volumes,
    describe_ebs_snapshots,
    describe_rds_instances,
    describe_load_balancers,
    describe_nat_gateways,
    describe_elastic_ips,
    list_s3_buckets,
)
from cost_optimizer.utilization import (
    get_ec2_utilization,
    get_rds_utilization,
    get_ebs_utilization,
    get_elb_utilization,
    get_nat_gateway_utilization,
    get_multi_resource_utilization,
)
from loguru import logger
from mcp.server.fastmcp import FastMCP, Context
from pydantic import Field
from cost_optimizer.auth import (
    get_active_sessions,
    close_client_session,
)
from typing import Any, Dict


# Configure Loguru logging
logger.remove()
logger.add(sys.stderr, level=os.getenv('FASTMCP_LOG_LEVEL', 'WARNING'))

# Define server instructions
SERVER_INSTRUCTIONS = """
# AWS Cost Optimizer MCP Server

## IMPORTANT: Each Cost Explorer API call costs $0.01 - use specific tools to minimize charges.

## Recommended Tool Flow

### Historical Analysis
- `get_cost_trend_with_anomalies` — Single call for 6-month trend + anomaly detection + drill-down.
  Includes today's date, top services, and usage type drivers. Replaces multiple get_cost_and_usage calls.

### Compute Analysis
- `list_ec2_regions_with_instances` — Discover regions with instances (running/stopped counts + types).
- `get_multi_resource_utilization` — EC2 + RDS utilization grouped by buckets (<5%, 5-20%, 20-50%, 50-80%, >80%).

### Storage Analysis
- `describe_ebs_volumes` — Compact volume info (id, size, type, state, attached instance).
- `describe_ebs_snapshots` — Compact snapshot info with orphan detection.
- `list_s3_buckets` — Boolean lifecycle flags (hasLifecycleRules, hasExpirationRules, hasTransitionRules).

### Savings Strategy
- `get_savings_commitments` — Existing SPs/RIs, coverage, utilization, and on-demand eligible spend.

### Granular Queries (when needed)
- `get_cost_and_usage` — Custom cost queries with filters and grouping ($0.01/call).
- `get_cost_and_usage_comparisons` — Compare two monthly periods.
- `get_cost_comparison_drivers` — Top 10 cost change drivers.
- `get_cost_forecast` — Predict future costs.
"""

# Get host configuration from environment (0.0.0.0 for container, 127.0.0.1 for local)
MCP_HOST = os.getenv('MCP_HOST', '127.0.0.1')
MCP_PORT = int(os.getenv('MCP_PORT', '8000'))

# Create FastMCP server with instructions
app = FastMCP(
    name='Cost Optimizer MCP Server',
    instructions=SERVER_INSTRUCTIONS,
    host=MCP_HOST,
    port=MCP_PORT,
)

# Register all tools with the app
app.tool('get_today_date')(get_today_date)
app.tool('get_dimension_values')(get_dimension_values)
app.tool('get_tag_values')(get_tag_values)
app.tool('get_cost_forecast')(get_cost_forecast)
app.tool('get_cost_and_usage_comparisons')(get_cost_and_usage_comparisons)
app.tool('get_cost_comparison_drivers')(get_cost_comparison_drivers)
app.tool('get_cost_and_usage')(get_cost_and_usage)
app.tool('get_cost_trend_with_anomalies')(get_cost_trend_with_anomalies)

# Register savings tools
app.tool('get_savings_commitments')(get_savings_commitments)

# Register inventory tools
app.tool('describe_ec2_instances')(describe_ec2_instances)
app.tool('list_ec2_regions_with_instances')(list_ec2_regions_with_instances)
app.tool('describe_ebs_volumes')(describe_ebs_volumes)
app.tool('describe_ebs_snapshots')(describe_ebs_snapshots)
app.tool('describe_rds_instances')(describe_rds_instances)
app.tool('describe_load_balancers')(describe_load_balancers)
app.tool('describe_nat_gateways')(describe_nat_gateways)
app.tool('describe_elastic_ips')(describe_elastic_ips)
app.tool('list_s3_buckets')(list_s3_buckets)

# Register utilization tools
app.tool('get_ec2_utilization')(get_ec2_utilization)
app.tool('get_rds_utilization')(get_rds_utilization)
app.tool('get_ebs_utilization')(get_ebs_utilization)
app.tool('get_elb_utilization')(get_elb_utilization)
app.tool('get_nat_gateway_utilization')(get_nat_gateway_utilization)
app.tool('get_multi_resource_utilization')(get_multi_resource_utilization)


@app.tool()
async def close_session(
    ctx: Context,
    client_id: str = Field(..., description='The client session ID to close'),
) -> Dict[str, Any]:
    """Close a specific client session and free up resources.
    
    Args:
        ctx: MCP context
        client_id: The session ID to close
        
    Returns:
        Status information about the closed session
    """
    return close_client_session(client_id)


@app.tool()
async def list_active_sessions(ctx: Context) -> Dict[str, Any]:
    """List all active Cost Explorer client sessions.
    
    Useful for monitoring and managing multiple concurrent clients.
    
    Args:
        ctx: MCP context
        
    Returns:
        Dictionary with information about all active sessions
    """
    return get_active_sessions()


def main():
    """Run the MCP server with CLI argument support.
    
    Transport can be configured via MCP_TRANSPORT environment variable:
    - 'stdio' (default): Standard input/output communication
    - 'sse': Server-Sent Events over HTTP (port 8000)
    - 'streamable-http': Streamable HTTP transport (port 8000)
    
    For SSE/HTTP transports, the server listens on port 8000 by default.
    Mount path can be configured via MCP_MOUNT_PATH environment variable.
    """
    transport = os.getenv('MCP_TRANSPORT', 'stdio')
    mount_path = os.getenv('MCP_MOUNT_PATH', None)
    
    if transport not in ('stdio', 'sse', 'streamable-http'):
        logger.warning(f"Invalid MCP_TRANSPORT '{transport}', defaulting to 'stdio'")
        transport = 'stdio'
    
    if mount_path:
        app.settings.mount_path = mount_path
        try:
            app.settings.streamable_http_path = app._normalize_path(mount_path, app.settings.streamable_http_path)
        except Exception:
            pass

    logger.info(f"Starting MCP server with transport: {transport}")
    app.run(transport=transport, mount_path=mount_path)


if __name__ == '__main__':
    main()
