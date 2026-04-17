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

"""EBS utilization handler."""

import os
from typing import Any, Dict, Optional

from loguru import logger
from mcp.server.fastmcp import Context
from pydantic import Field

from ..aws_clients import get_cloudwatch_client
from .common import calculate_time_range, get_metric_statistics


async def get_ebs_utilization(
    ctx: Context,
    client_id: str = Field(
        ...,
        description="Client identifier to use for this request."
    ),
    volume_id: str = Field(
        ...,
        description="EBS Volume ID."
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
    """Get EBS volume utilization metrics from CloudWatch.

    Returns IOPS and throughput metrics for an EBS volume.
    """
    try:
        cw = get_cloudwatch_client(client_id, region)
        start_time, end_time = calculate_time_range(days_back)
        
        dimensions = [{'Name': 'VolumeId', 'Value': volume_id}]
        namespace = 'AWS/EBS'
        
        metrics = {}
        
        # IOPS
        metrics['read_ops'] = get_metric_statistics(
            cw, namespace, 'VolumeReadOps',
            dimensions, start_time, end_time, period_seconds,
            ['Sum', 'Average']
        )
        
        metrics['write_ops'] = get_metric_statistics(
            cw, namespace, 'VolumeWriteOps',
            dimensions, start_time, end_time, period_seconds,
            ['Sum', 'Average']
        )
        
        # Throughput
        metrics['read_bytes'] = get_metric_statistics(
            cw, namespace, 'VolumeReadBytes',
            dimensions, start_time, end_time, period_seconds,
            ['Sum', 'Average']
        )
        
        metrics['write_bytes'] = get_metric_statistics(
            cw, namespace, 'VolumeWriteBytes',
            dimensions, start_time, end_time, period_seconds,
            ['Sum', 'Average']
        )
        
        # Queue length
        metrics['queue_length'] = get_metric_statistics(
            cw, namespace, 'VolumeQueueLength',
            dimensions, start_time, end_time, period_seconds,
            ['Average', 'Maximum']
        )
        
        # Idle time
        metrics['idle_time'] = get_metric_statistics(
            cw, namespace, 'VolumeIdleTime',
            dimensions, start_time, end_time, period_seconds,
            ['Sum', 'Average']
        )
        
        # Burst balance (for gp2 volumes)
        metrics['burst_balance'] = get_metric_statistics(
            cw, namespace, 'BurstBalance',
            dimensions, start_time, end_time, period_seconds,
            ['Average', 'Minimum']
        )
        
        # Calculate total I/O
        total_read_ops = sum(dp.get('Sum', 0) for dp in metrics.get('read_ops', {}).get('datapoints', []))
        total_write_ops = sum(dp.get('Sum', 0) for dp in metrics.get('write_ops', {}).get('datapoints', []))
        total_ops = total_read_ops + total_write_ops
        
        avg_iops = total_ops / (days_back * 24 * 3600) if total_ops > 0 else 0
        
        assessment = {
            'total_read_ops': total_read_ops,
            'total_write_ops': total_write_ops,
            'average_iops': round(avg_iops, 2),
            'recommendations': [],
        }
        
        if total_ops == 0:
            assessment['status'] = 'idle'
            assessment['recommendations'].append(
                'No I/O activity - volume may be unused'
            )
        elif avg_iops < 100:
            assessment['status'] = 'low_utilization'
            assessment['recommendations'].append(
                'Low IOPS usage - consider gp3 with lower provisioned IOPS'
            )
        else:
            assessment['status'] = 'active'
        
        return {
            'volume_id': volume_id,
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
        logger.error(f'Error getting EBS utilization: {e}')
        return {'error': str(e)}
