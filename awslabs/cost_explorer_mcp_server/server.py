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

"""Cost Explorer MCP server implementation.

This server provides tools for analyzing AWS costs and usage data through the AWS Cost Explorer API.
"""

import os
import sys
from awslabs.cost_explorer_mcp_server.comparison_handler import (
    get_cost_and_usage_comparisons,
    get_cost_comparison_drivers,
)
from awslabs.cost_explorer_mcp_server.cost_usage_handler import get_cost_and_usage
from awslabs.cost_explorer_mcp_server.forecasting_handler import get_cost_forecast
from awslabs.cost_explorer_mcp_server.metadata_handler import (
    get_dimension_values,
    get_tag_values,
)
from awslabs.cost_explorer_mcp_server.utility_handler import get_today_date
from awslabs.cost_explorer_mcp_server.inventory import (
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
from awslabs.cost_explorer_mcp_server.utilization import (
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
from awslabs.cost_explorer_mcp_server.auth import (
    get_active_sessions,
    close_client_session,
)
from typing import Any, Dict


# Configure Loguru logging
logger.remove()
logger.add(sys.stderr, level=os.getenv('FASTMCP_LOG_LEVEL', 'WARNING'))

# Define server instructions
SERVER_INSTRUCTIONS = """
# AWS Cost Explorer MCP Server

## IMPORTANT: Each API call costs $0.01 - use filters and specific date ranges to minimize charges.

## Critical Rules
- Comparison periods: exactly 1 month, start on day 1 (e.g., "2025-04-01" to "2025-05-01")
- UsageQuantity: Recommended to filter by USAGE_TYPE, USAGE_TYPE_GROUP or results are meaningless
- When user says "last X months": Use complete calendar months, not partial periods
- get_cost_comparison_drivers: returns only top 10 most significant drivers

## Query Pattern Mapping

| User Query Pattern | Recommended Tool | Notes |
|-------------------|-----------------|-------|
| "What were my costs for..." | get_cost_and_usage | Use for historical cost analysis |
| "How much did I spend on..." | get_cost_and_usage | Filter by service/region as needed |
| "Show me costs by..." | get_cost_and_usage | Set group_by parameter accordingly |
| "Compare costs between..." | get_cost_and_usage_comparisons | Ensure exactly 1 month periods |
| "Why did my costs change..." | get_cost_comparison_drivers | Returns top 10 drivers only |
| "What caused my bill to..." | get_cost_comparison_drivers | Good for root cause analysis |
| "Predict/forecast my costs..." | get_cost_forecast | Works best with specific services |
| "What will I spend on..." | get_cost_forecast | Can filter by dimension |

## Cost Optimization Tips
- Always use specific date ranges rather than broad periods
- Filter by specific services when possible to reduce data processed
- For usage metrics, always filter by USAGE_TYPE or USAGE_TYPE_GROUP to get meaningful results
- Combine related questions into a single query where possible
"""

# Get host configuration from environment (0.0.0.0 for container, 127.0.0.1 for local)
MCP_HOST = os.getenv('MCP_HOST', '127.0.0.1')
MCP_PORT = int(os.getenv('MCP_PORT', '8000'))

# Create FastMCP server with instructions
app = FastMCP(
    name='Cost Explorer MCP Server',
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
    
    logger.info(f"Starting MCP server with transport: {transport}")
    app.run(transport=transport, mount_path=mount_path)


if __name__ == '__main__':
    main()
