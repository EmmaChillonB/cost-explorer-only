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

"""ELB utilization handler."""

import os
from typing import Any, Dict, Optional

from loguru import logger
from mcp.server.fastmcp import Context
from pydantic import Field

from ..aws_clients import get_cloudwatch_client
from .common import calculate_time_range, get_metric_statistics


def _assess_elb_utilization(metrics: Dict[str, Any], lb_type: str) -> Dict[str, Any]:
    """Assess ELB utilization and provide recommendations."""
    assessment = {
        'status': 'unknown',
        'recommendations': [],
    }
    
    if lb_type in ['application', 'classic']:
        request_sum = 0
        request_data = metrics.get('request_count', {}).get('datapoints', [])
        for dp in request_data:
            request_sum += dp.get('Sum', 0)
        
        if request_sum == 0:
            assessment['status'] = 'unused'
            assessment['recommendations'].append(
                'No requests in the period - consider removing if not needed'
            )
        elif request_sum < 1000:
            assessment['status'] = 'low_traffic'
            assessment['recommendations'].append(
                'Very low traffic - consider if load balancer is necessary'
            )
        else:
            assessment['status'] = 'active'
    
    return assessment


async def get_elb_utilization(
    ctx: Context,
    client_id: str = Field(
        ...,
        description="Client identifier to use for this request."
    ),
    load_balancer_name: str = Field(
        ...,
        description="Load balancer name."
    ),
    load_balancer_type: str = Field(
        'application',
        description="Type: 'application' (ALB), 'network' (NLB), or 'classic'."
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
    """Get Load Balancer utilization metrics from CloudWatch.

    Returns request count, connection metrics, and response codes for load balancers.
    """
    try:
        cw = get_cloudwatch_client(client_id, region)
        start_time, end_time = calculate_time_range(days_back)
        
        metrics = {}
        
        if load_balancer_type == 'application':
            dimensions = [{'Name': 'LoadBalancer', 'Value': load_balancer_name}]
            namespace = 'AWS/ApplicationELB'
            
            metrics['request_count'] = get_metric_statistics(
                cw, namespace, 'RequestCount',
                dimensions, start_time, end_time, period_seconds,
                ['Sum', 'Average']
            )
            
            metrics['target_response_time'] = get_metric_statistics(
                cw, namespace, 'TargetResponseTime',
                dimensions, start_time, end_time, period_seconds,
                ['Average', 'Maximum']
            )
            
            metrics['http_2xx_count'] = get_metric_statistics(
                cw, namespace, 'HTTPCode_Target_2XX_Count',
                dimensions, start_time, end_time, period_seconds,
                ['Sum']
            )
            
            metrics['http_4xx_count'] = get_metric_statistics(
                cw, namespace, 'HTTPCode_Target_4XX_Count',
                dimensions, start_time, end_time, period_seconds,
                ['Sum']
            )
            
            metrics['http_5xx_count'] = get_metric_statistics(
                cw, namespace, 'HTTPCode_Target_5XX_Count',
                dimensions, start_time, end_time, period_seconds,
                ['Sum']
            )
            
            metrics['active_connection_count'] = get_metric_statistics(
                cw, namespace, 'ActiveConnectionCount',
                dimensions, start_time, end_time, period_seconds,
                ['Average', 'Maximum']
            )
            
            metrics['new_connection_count'] = get_metric_statistics(
                cw, namespace, 'NewConnectionCount',
                dimensions, start_time, end_time, period_seconds,
                ['Sum']
            )
            
        elif load_balancer_type == 'network':
            dimensions = [{'Name': 'LoadBalancer', 'Value': load_balancer_name}]
            namespace = 'AWS/NetworkELB'
            
            metrics['active_flow_count'] = get_metric_statistics(
                cw, namespace, 'ActiveFlowCount',
                dimensions, start_time, end_time, period_seconds,
                ['Average', 'Maximum']
            )
            
            metrics['new_flow_count'] = get_metric_statistics(
                cw, namespace, 'NewFlowCount',
                dimensions, start_time, end_time, period_seconds,
                ['Sum']
            )
            
            metrics['processed_bytes'] = get_metric_statistics(
                cw, namespace, 'ProcessedBytes',
                dimensions, start_time, end_time, period_seconds,
                ['Sum', 'Average']
            )
            
        else:  # classic
            dimensions = [{'Name': 'LoadBalancerName', 'Value': load_balancer_name}]
            namespace = 'AWS/ELB'
            
            metrics['request_count'] = get_metric_statistics(
                cw, namespace, 'RequestCount',
                dimensions, start_time, end_time, period_seconds,
                ['Sum']
            )
            
            metrics['latency'] = get_metric_statistics(
                cw, namespace, 'Latency',
                dimensions, start_time, end_time, period_seconds,
                ['Average', 'Maximum']
            )
            
            metrics['healthy_host_count'] = get_metric_statistics(
                cw, namespace, 'HealthyHostCount',
                dimensions, start_time, end_time, period_seconds,
                ['Average', 'Minimum']
            )
            
            metrics['unhealthy_host_count'] = get_metric_statistics(
                cw, namespace, 'UnHealthyHostCount',
                dimensions, start_time, end_time, period_seconds,
                ['Average', 'Maximum']
            )
        
        assessment = _assess_elb_utilization(metrics, load_balancer_type)
        
        return {
            'load_balancer_name': load_balancer_name,
            'load_balancer_type': load_balancer_type,
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
        logger.error(f'Error getting ELB utilization: {e}')
        return {'error': str(e)}
