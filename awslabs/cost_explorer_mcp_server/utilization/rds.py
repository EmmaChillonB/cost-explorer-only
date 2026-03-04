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

"""RDS utilization handler."""

import os
from typing import Any, Dict, Optional

from loguru import logger
from mcp.server.fastmcp import Context
from pydantic import Field

from ..aws_clients import get_cloudwatch_client
from .common import calculate_time_range, get_metric_statistics


def _assess_rds_utilization(metrics: Dict[str, Any]) -> Dict[str, Any]:
    """Assess RDS utilization and provide recommendations."""
    assessment = {
        'status': 'unknown',
        'recommendations': [],
    }
    
    cpu_avg = metrics.get('cpu', {}).get('summary', {}).get('overall_average')
    
    if cpu_avg is not None:
        if cpu_avg < 10:
            assessment['status'] = 'underutilized'
            assessment['recommendations'].append(
                'CPU average below 10% - consider downsizing instance class'
            )
        elif cpu_avg > 80:
            assessment['status'] = 'highly_utilized'
            assessment['recommendations'].append(
                'CPU average above 80% - consider upsizing instance class or read replicas'
            )
        else:
            assessment['status'] = 'appropriately_sized'
    
    conn_avg = metrics.get('connections', {}).get('summary', {}).get('overall_average')
    if conn_avg is not None and conn_avg < 5:
        assessment['recommendations'].append(
            'Very low connection count - database may be underutilized'
        )
    
    free_storage_min = metrics.get('free_storage_space', {}).get('summary', {}).get('overall_minimum')
    if free_storage_min is not None:
        free_storage_gb = free_storage_min / (1024 ** 3)
        if free_storage_gb > 100:
            assessment['recommendations'].append(
                f'Over {free_storage_gb:.0f}GB free storage - consider reducing allocated storage'
            )
    
    return assessment


async def get_rds_utilization(
    ctx: Context,
    client_id: str = Field(
        ...,
        description="Client identifier to use for this request."
    ),
    db_instance_identifier: str = Field(
        ...,
        description="RDS database instance identifier."
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
    """Get RDS database utilization metrics from CloudWatch.

    Returns CPU, connections, storage, and I/O metrics for an RDS instance.
    """
    try:
        cw = get_cloudwatch_client(client_id, region)
        start_time, end_time = calculate_time_range(days_back)
        
        dimensions = [{'Name': 'DBInstanceIdentifier', 'Value': db_instance_identifier}]
        
        metrics = {}
        
        # CPU Utilization
        metrics['cpu'] = get_metric_statistics(
            cw, 'AWS/RDS', 'CPUUtilization',
            dimensions, start_time, end_time, period_seconds,
            ['Average', 'Maximum', 'Minimum']
        )
        
        # Database Connections
        metrics['connections'] = get_metric_statistics(
            cw, 'AWS/RDS', 'DatabaseConnections',
            dimensions, start_time, end_time, period_seconds,
            ['Average', 'Maximum', 'Minimum']
        )
        
        # Free Storage Space
        metrics['free_storage_space'] = get_metric_statistics(
            cw, 'AWS/RDS', 'FreeStorageSpace',
            dimensions, start_time, end_time, period_seconds,
            ['Average', 'Minimum']
        )
        
        # IOPS
        metrics['read_iops'] = get_metric_statistics(
            cw, 'AWS/RDS', 'ReadIOPS',
            dimensions, start_time, end_time, period_seconds,
            ['Average', 'Maximum', 'Sum']
        )
        
        metrics['write_iops'] = get_metric_statistics(
            cw, 'AWS/RDS', 'WriteIOPS',
            dimensions, start_time, end_time, period_seconds,
            ['Average', 'Maximum', 'Sum']
        )
        
        # Latency
        metrics['read_latency'] = get_metric_statistics(
            cw, 'AWS/RDS', 'ReadLatency',
            dimensions, start_time, end_time, period_seconds,
            ['Average', 'Maximum']
        )
        
        metrics['write_latency'] = get_metric_statistics(
            cw, 'AWS/RDS', 'WriteLatency',
            dimensions, start_time, end_time, period_seconds,
            ['Average', 'Maximum']
        )
        
        # Freeable Memory
        metrics['freeable_memory'] = get_metric_statistics(
            cw, 'AWS/RDS', 'FreeableMemory',
            dimensions, start_time, end_time, period_seconds,
            ['Average', 'Minimum']
        )
        
        # Network Throughput
        metrics['network_receive_throughput'] = get_metric_statistics(
            cw, 'AWS/RDS', 'NetworkReceiveThroughput',
            dimensions, start_time, end_time, period_seconds,
            ['Average', 'Maximum']
        )
        
        metrics['network_transmit_throughput'] = get_metric_statistics(
            cw, 'AWS/RDS', 'NetworkTransmitThroughput',
            dimensions, start_time, end_time, period_seconds,
            ['Average', 'Maximum']
        )
        
        assessment = _assess_rds_utilization(metrics)
        
        return {
            'db_instance_identifier': db_instance_identifier,
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
        logger.error(f'Error getting RDS utilization: {e}')
        return {'error': str(e)}
