"""Bulk utilization helpers — one call per resource type (EC2 or RDS)."""

from typing import Any, Dict, List, Optional

from mcp.server.fastmcp import Context
from pydantic import Field

from .multi import get_multi_resource_utilization


async def get_ec2_utilization(
    ctx: Context,
    client_id: str = Field(..., description='Client ID for AWS session'),
    region: Optional[str] = Field(None, description='AWS region'),
    days_back: int = Field(14, description='Days of metrics to retrieve'),
    ec2_filters: Optional[List[Dict[str, Any]]] = Field(None, description='EC2 filters'),
) -> Dict[str, Any]:
    """Discover and analyze all EC2 instances in a region, grouped by CPU utilization buckets."""
    return await get_multi_resource_utilization(
        ctx=ctx,
        client_id=client_id,
        region=region,
        include_ec2=True,
        include_rds=False,
        days_back=days_back,
        ec2_filters=ec2_filters,
    )


async def get_rds_utilization(
    ctx: Context,
    client_id: str = Field(..., description='Client ID for AWS session'),
    region: Optional[str] = Field(None, description='AWS region'),
    days_back: int = Field(14, description='Days of metrics to retrieve'),
) -> Dict[str, Any]:
    """Discover and analyze all RDS instances in a region, grouped by CPU utilization buckets."""
    return await get_multi_resource_utilization(
        ctx=ctx,
        client_id=client_id,
        region=region,
        include_ec2=False,
        include_rds=True,
        days_back=days_back,
    )
