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

"""Load Balancer inventory handler — pre-analyzed for cost optimization.

Returns a summary of all LBs plus only the problematic ones in detail
(no healthy targets, idle LBs).
"""

import os
from typing import Any, Dict, Optional

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
) -> Dict[str, Any]:
    """Analyze Load Balancers for cost optimization.

    Returns a summary with count by type, plus detail only for LBs
    with no healthy targets (candidates for removal/investigation).
    Well-functioning LBs are only counted in the summary.
    """
    try:
        elbv2 = get_elbv2_client(client_id, region)
        target_region = region or os.environ.get('AWS_REGION', 'eu-west-1')

        paginator = elbv2.get_paginator('describe_load_balancers')

        total = 0
        by_type: Dict[str, int] = {}
        lbs_no_targets = []
        lbs_all_unhealthy = []

        for page in paginator.paginate():
            for lb in page.get('LoadBalancers', []):
                total += 1
                lb_type = lb.get('Type', 'unknown')
                by_type[lb_type] = by_type.get(lb_type, 0) + 1

                lb_arn = lb.get('LoadBalancerArn')
                lb_name = lb.get('LoadBalancerName')

                # Check target health
                total_targets = 0
                healthy_targets = 0
                try:
                    tg_resp = elbv2.describe_target_groups(LoadBalancerArn=lb_arn)
                    for tg in tg_resp.get('TargetGroups', []):
                        try:
                            health_resp = elbv2.describe_target_health(
                                TargetGroupArn=tg.get('TargetGroupArn')
                            )
                            for t in health_resp.get('TargetHealthDescriptions', []):
                                total_targets += 1
                                if t.get('TargetHealth', {}).get('State') == 'healthy':
                                    healthy_targets += 1
                        except Exception:
                            pass
                except Exception:
                    pass

                lb_entry = serialize_datetime({
                    'Name': lb_name,
                    'Type': lb_type,
                    'Scheme': lb.get('Scheme'),
                    'State': lb.get('State', {}).get('Code'),
                    'CreatedTime': lb.get('CreatedTime'),
                    'TotalTargets': total_targets,
                    'HealthyTargets': healthy_targets,
                })

                if total_targets == 0:
                    lbs_no_targets.append(lb_entry)
                elif healthy_targets == 0:
                    lbs_all_unhealthy.append(lb_entry)

        return {
            'summary': {
                'total': total,
                'by_type': by_type,
                'with_no_targets': len(lbs_no_targets),
                'with_all_unhealthy': len(lbs_all_unhealthy),
            },
            'lbs_no_targets': lbs_no_targets,
            'lbs_all_unhealthy': lbs_all_unhealthy,
            'region': target_region,
        }

    except Exception as e:
        logger.error(f'Error describing load balancers: {e}')
        return {'error': str(e)}
