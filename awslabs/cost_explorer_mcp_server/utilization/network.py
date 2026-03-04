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

"""NAT Gateway utilization handler."""

import os
from typing import Any, Dict, Optional

from loguru import logger
from mcp.server.fastmcp import Context
from pydantic import Field

from ..aws_clients import get_cloudwatch_client
from .common import calculate_time_range, get_metric_statistics


async def get_nat_gateway_utilization(
    ctx: Context,
    client_id: str = Field(
        ...,
        description="Client identifier to use for this request."
    ),
    nat_gateway_id: str = Field(
        ...,
        description="NAT Gateway ID."
    ),
    region: Optional[str] = Field(
        None,
        description="AWS region."
    ),
    days_back: int = Field(
        7,
        description="Number of days to look back for metrics."
    ),
    period_seconds: int = Field(
        3600,
        description="Period in seconds for metric aggregation."
    ),
) -> Dict[str, Any]:
    """Get NAT Gateway utilization metrics from CloudWatch.

    NAT Gateways are expensive - $0.045/hour plus data processing charges.
    """
    try:
        cw = get_cloudwatch_client(client_id, region)
        start_time, end_time = calculate_time_range(days_back)
        
        dimensions = [{'Name': 'NatGatewayId', 'Value': nat_gateway_id}]
        namespace = 'AWS/NATGateway'
        
        metrics = {}
        
        # Bytes transferred
        metrics['bytes_in_from_destination'] = get_metric_statistics(
            cw, namespace, 'BytesInFromDestination',
            dimensions, start_time, end_time, period_seconds,
            ['Sum', 'Average']
        )
        
        metrics['bytes_in_from_source'] = get_metric_statistics(
            cw, namespace, 'BytesInFromSource',
            dimensions, start_time, end_time, period_seconds,
            ['Sum', 'Average']
        )
        
        metrics['bytes_out_to_destination'] = get_metric_statistics(
            cw, namespace, 'BytesOutToDestination',
            dimensions, start_time, end_time, period_seconds,
            ['Sum', 'Average']
        )
        
        metrics['bytes_out_to_source'] = get_metric_statistics(
            cw, namespace, 'BytesOutToSource',
            dimensions, start_time, end_time, period_seconds,
            ['Sum', 'Average']
        )
        
        # Connection metrics
        metrics['active_connection_count'] = get_metric_statistics(
            cw, namespace, 'ActiveConnectionCount',
            dimensions, start_time, end_time, period_seconds,
            ['Average', 'Maximum']
        )
        
        metrics['connection_attempt_count'] = get_metric_statistics(
            cw, namespace, 'ConnectionAttemptCount',
            dimensions, start_time, end_time, period_seconds,
            ['Sum']
        )
        
        metrics['connection_established_count'] = get_metric_statistics(
            cw, namespace, 'ConnectionEstablishedCount',
            dimensions, start_time, end_time, period_seconds,
            ['Sum']
        )
        
        # Packets
        metrics['packets_in_from_source'] = get_metric_statistics(
            cw, namespace, 'PacketsInFromSource',
            dimensions, start_time, end_time, period_seconds,
            ['Sum']
        )
        
        metrics['packets_out_to_destination'] = get_metric_statistics(
            cw, namespace, 'PacketsOutToDestination',
            dimensions, start_time, end_time, period_seconds,
            ['Sum']
        )
        
        # Calculate data transfer costs
        total_bytes = 0
        for key in ['bytes_in_from_source', 'bytes_out_to_destination']:
            for dp in metrics.get(key, {}).get('datapoints', []):
                total_bytes += dp.get('Sum', 0)
        
        total_gb = total_bytes / (1024 ** 3)
        estimated_data_cost = total_gb * 0.045
        hourly_cost = 0.045 * 24 * days_back
        total_estimated_cost = estimated_data_cost + hourly_cost
        
        assessment = {
            'total_data_transfer_gb': round(total_gb, 2),
            'estimated_data_processing_cost_usd': round(estimated_data_cost, 2),
            'estimated_hourly_cost_usd': round(hourly_cost, 2),
            'estimated_total_cost_usd': round(total_estimated_cost, 2),
            'recommendations': [],
        }
        
        if total_gb < 1:
            assessment['recommendations'].append(
                'Very low data transfer - consider if NAT Gateway is necessary'
            )
        
        conn_max = metrics.get('active_connection_count', {}).get('summary', {}).get('overall_maximum')
        if conn_max is not None and conn_max < 10:
            assessment['recommendations'].append(
                'Low connection count - could potentially use NAT instance for cost savings'
            )
        
        return {
            'nat_gateway_id': nat_gateway_id,
            'region': region or os.environ.get('AWS_REGION', 'eu-west-1'),
            'period': {
                'start': start_time.isoformat(),
                'end': end_time.isoformat(),
                'days': days_back,
            },
            'metrics': metrics,
            'assessment': assessment,
        }
        
    except Exception as e:
        logger.error(f'Error getting NAT Gateway utilization: {e}')
        return {'error': str(e)}
