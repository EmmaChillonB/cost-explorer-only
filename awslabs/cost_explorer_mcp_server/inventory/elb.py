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

"""Load Balancer inventory handler."""

import os
from typing import Any, Dict, List, Optional

from loguru import logger
from mcp.server.fastmcp import Context
from pydantic import Field

from ..aws_clients import get_elbv2_client
from .common import serialize_datetime


async def describe_load_balancers(
    ctx: Context,
    client_id: str = Field(
        ...,
        description="Client identifier to use for this request."
    ),
    region: Optional[str] = Field(
        None,
        description="AWS region to query."
    ),
    load_balancer_arns: Optional[List[str]] = Field(
        None,
        description="List of specific load balancer ARNs to describe."
    ),
    names: Optional[List[str]] = Field(
        None,
        description="List of specific load balancer names to describe."
    ),
    include_target_health: bool = Field(
        True,
        description="Include target group health information."
    ),
) -> Dict[str, Any]:
    """Describe Application/Network Load Balancers with target health.

    Returns information about ALBs and NLBs including their target groups
    and target health status.
    """
    try:
        elbv2 = get_elbv2_client(client_id, region)
        
        params = {}
        if load_balancer_arns:
            params['LoadBalancerArns'] = load_balancer_arns
        if names:
            params['Names'] = names
        
        load_balancers = []
        paginator = elbv2.get_paginator('describe_load_balancers')
        
        for page in paginator.paginate(**params):
            for lb in page.get('LoadBalancers', []):
                lb_info = {
                    'LoadBalancerArn': lb.get('LoadBalancerArn'),
                    'LoadBalancerName': lb.get('LoadBalancerName'),
                    'Type': lb.get('Type'),
                    'Scheme': lb.get('Scheme'),
                    'State': lb.get('State', {}).get('Code'),
                    'VpcId': lb.get('VpcId'),
                    'AvailabilityZones': [
                        {
                            'ZoneName': az.get('ZoneName'),
                            'SubnetId': az.get('SubnetId'),
                        }
                        for az in lb.get('AvailabilityZones', [])
                    ],
                    'CreatedTime': lb.get('CreatedTime'),
                    'IpAddressType': lb.get('IpAddressType'),
                    'TargetGroups': [],
                }
                
                if include_target_health:
                    try:
                        tg_response = elbv2.describe_target_groups(
                            LoadBalancerArn=lb.get('LoadBalancerArn')
                        )
                        
                        for tg in tg_response.get('TargetGroups', []):
                            tg_info = {
                                'TargetGroupArn': tg.get('TargetGroupArn'),
                                'TargetGroupName': tg.get('TargetGroupName'),
                                'Protocol': tg.get('Protocol'),
                                'Port': tg.get('Port'),
                                'TargetType': tg.get('TargetType'),
                                'HealthCheckEnabled': tg.get('HealthCheckEnabled'),
                                'Targets': [],
                            }
                            
                            try:
                                health_response = elbv2.describe_target_health(
                                    TargetGroupArn=tg.get('TargetGroupArn')
                                )
                                
                                healthy_count = 0
                                unhealthy_count = 0
                                
                                for target in health_response.get('TargetHealthDescriptions', []):
                                    state = target.get('TargetHealth', {}).get('State')
                                    if state == 'healthy':
                                        healthy_count += 1
                                    else:
                                        unhealthy_count += 1
                                    
                                    tg_info['Targets'].append({
                                        'Target': target.get('Target'),
                                        'HealthState': state,
                                        'HealthDescription': target.get('TargetHealth', {}).get('Description'),
                                    })
                                
                                tg_info['HealthyTargetCount'] = healthy_count
                                tg_info['UnhealthyTargetCount'] = unhealthy_count
                                tg_info['TotalTargetCount'] = healthy_count + unhealthy_count
                                
                            except Exception as health_error:
                                logger.warning(f'Error getting target health: {health_error}')
                                tg_info['HealthError'] = str(health_error)
                            
                            lb_info['TargetGroups'].append(tg_info)
                            
                    except Exception as tg_error:
                        logger.warning(f'Error getting target groups: {tg_error}')
                        lb_info['TargetGroupError'] = str(tg_error)
                
                load_balancers.append(serialize_datetime(lb_info))
        
        lbs_with_no_targets = sum(
            1 for lb in load_balancers 
            if all(tg.get('TotalTargetCount', 0) == 0 for tg in lb.get('TargetGroups', []))
            and lb.get('TargetGroups')
        )
        
        return {
            'load_balancers': load_balancers,
            'count': len(load_balancers),
            'load_balancers_with_no_targets': lbs_with_no_targets,
            'region': region or os.environ.get('AWS_REGION', 'eu-west-1'),
        }
        
    except Exception as e:
        logger.error(f'Error describing load balancers: {e}')
        return {'error': str(e)}
