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

"""EC2 utilization handler."""

import os
from typing import Any, Dict, Optional

from loguru import logger
from mcp.server.fastmcp import Context
from pydantic import Field

from ..aws_clients import get_cloudwatch_client
from .common import calculate_time_range, get_metric_statistics


def _assess_ec2_utilization(metrics: Dict[str, Any]) -> Dict[str, Any]:
    """Assess EC2 utilization and provide recommendations."""
    assessment = {
        'status': 'unknown',
        'recommendations': [],
    }
    
    cpu_avg = metrics.get('cpu', {}).get('summary', {}).get('overall_average')
    
    if cpu_avg is not None:
        if cpu_avg < 5:
            assessment['status'] = 'significantly_underutilized'
            assessment['recommendations'].append(
                'CPU average below 5% - consider downsizing instance type'
            )
        elif cpu_avg < 20:
            assessment['status'] = 'underutilized'
            assessment['recommendations'].append(
                'CPU average below 20% - may be able to downsize instance type'
            )
        elif cpu_avg > 80:
            assessment['status'] = 'highly_utilized'
            assessment['recommendations'].append(
                'CPU average above 80% - consider upsizing or adding auto-scaling'
            )
        else:
            assessment['status'] = 'appropriately_sized'
    
    credit_balance = metrics.get('cpu_credit_balance', {}).get('summary', {}).get('overall_average')
    if credit_balance is not None and credit_balance < 10:
        assessment['recommendations'].append(
            'Low CPU credit balance - instance may need unlimited mode or larger size'
        )
    
    return assessment


async def get_ec2_utilization(
    ctx: Context,
    client_id: str = Field(
        ...,
        description="Client identifier to use for this request."
    ),
    instance_id: str = Field(
        ...,
        description="EC2 instance ID to get utilization metrics for."
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
    include_memory: bool = Field(
        False,
        description="Include memory metrics (requires CloudWatch Agent)."
    ),
    include_disk: bool = Field(
        False,
        description="Include disk metrics (requires CloudWatch Agent)."
    ),
) -> Dict[str, Any]:
    """Get EC2 instance utilization metrics from CloudWatch.

    Returns CPU, Network, and optionally Memory/Disk metrics for an EC2 instance.
    """
    try:
        target_region = region or os.environ.get('AWS_REGION', 'eu-west-1')
        logger.info(f'Getting EC2 utilization for {instance_id} in {target_region}, last {days_back} days')
        
        cw = get_cloudwatch_client(client_id, region)
        start_time, end_time = calculate_time_range(days_back)
        
        logger.debug(f'Time range: {start_time} to {end_time}')
        
        dimensions = [{'Name': 'InstanceId', 'Value': instance_id}]
        
        metrics = {}
        
        # CPU Utilization
        metrics['cpu'] = get_metric_statistics(
            cw, 'AWS/EC2', 'CPUUtilization',
            dimensions, start_time, end_time, period_seconds,
            ['Average', 'Maximum', 'Minimum']
        )
        
        # Network metrics
        metrics['network_in'] = get_metric_statistics(
            cw, 'AWS/EC2', 'NetworkIn',
            dimensions, start_time, end_time, period_seconds,
            ['Average', 'Sum', 'Maximum']
        )
        
        metrics['network_out'] = get_metric_statistics(
            cw, 'AWS/EC2', 'NetworkOut',
            dimensions, start_time, end_time, period_seconds,
            ['Average', 'Sum', 'Maximum']
        )
        
        # Disk I/O
        metrics['disk_read_ops'] = get_metric_statistics(
            cw, 'AWS/EC2', 'DiskReadOps',
            dimensions, start_time, end_time, period_seconds,
            ['Average', 'Sum']
        )
        
        metrics['disk_write_ops'] = get_metric_statistics(
            cw, 'AWS/EC2', 'DiskWriteOps',
            dimensions, start_time, end_time, period_seconds,
            ['Average', 'Sum']
        )
        
        # CPU Credit metrics (for burstable instances)
        metrics['cpu_credit_balance'] = get_metric_statistics(
            cw, 'AWS/EC2', 'CPUCreditBalance',
            dimensions, start_time, end_time, period_seconds,
            ['Average', 'Minimum']
        )
        
        metrics['cpu_credit_usage'] = get_metric_statistics(
            cw, 'AWS/EC2', 'CPUCreditUsage',
            dimensions, start_time, end_time, period_seconds,
            ['Average', 'Sum']
        )
        
        # Memory metrics (requires CloudWatch Agent)
        if include_memory:
            mem_dimensions = [{'Name': 'InstanceId', 'Value': instance_id}]
            metrics['memory_used_percent'] = get_metric_statistics(
                cw, 'CWAgent', 'mem_used_percent',
                mem_dimensions, start_time, end_time, period_seconds,
                ['Average', 'Maximum', 'Minimum']
            )
        
        # Disk metrics (requires CloudWatch Agent)
        if include_disk:
            disk_dimensions = [{'Name': 'InstanceId', 'Value': instance_id}]
            metrics['disk_used_percent'] = get_metric_statistics(
                cw, 'CWAgent', 'disk_used_percent',
                disk_dimensions, start_time, end_time, period_seconds,
                ['Average', 'Maximum']
            )
        
        assessment = _assess_ec2_utilization(metrics)
        
        return {
            'instance_id': instance_id,
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
        logger.error(f'Error getting EC2 utilization: {e}')
        return {'error': str(e)}
