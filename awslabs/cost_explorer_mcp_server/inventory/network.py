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

"""Network resources inventory handler — pre-analyzed for cost optimization.

NAT Gateways and Elastic IPs are returned with summary + actionable items only.
"""

import os
from typing import Any, Dict, Optional

from loguru import logger
from mcp.server.fastmcp import Context
from pydantic import Field

from ..aws_clients import get_ec2_client
from .common import serialize_datetime


async def describe_nat_gateways(
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
    """Analyze NAT Gateways for cost optimization.

    NAT Gateways cost ~$32.40/month each ($0.045/hour) plus data processing.
    Returns a summary with count and cost estimate, plus a compact list
    with only the fields relevant for optimization decisions.
    """
    try:
        ec2 = get_ec2_client(client_id, region)
        target_region = region or os.environ.get('AWS_REGION', 'eu-west-1')

        paginator = ec2.get_paginator('describe_nat_gateways')

        active = 0
        total = 0
        nat_list = []
        vpc_counts: Dict[str, int] = {}

        for page in paginator.paginate():
            for nat in page.get('NatGateways', []):
                total += 1
                state = nat.get('State')
                vpc_id = nat.get('VpcId', 'unknown')

                if state == 'available':
                    active += 1
                    vpc_counts[vpc_id] = vpc_counts.get(vpc_id, 0) + 1

                nat_list.append(serialize_datetime({
                    'NatGatewayId': nat.get('NatGatewayId'),
                    'State': state,
                    'VpcId': vpc_id,
                    'SubnetId': nat.get('SubnetId'),
                    'ConnectivityType': nat.get('ConnectivityType'),
                    'CreateTime': nat.get('CreateTime'),
                }))

        estimated_monthly = round(active * 0.045 * 24 * 30, 2)

        # Flag VPCs with multiple NAT GWs (possible consolidation)
        vpcs_with_multiple = {
            vpc: count for vpc, count in vpc_counts.items() if count > 1
        }

        return {
            'summary': {
                'total': total,
                'active': active,
                'estimated_monthly_base_cost_usd': estimated_monthly,
                'vpcs_with_multiple_nat_gws': vpcs_with_multiple,
            },
            'nat_gateways': nat_list,
            'region': target_region,
        }

    except Exception as e:
        logger.error(f'Error describing NAT Gateways: {e}')
        return {'error': str(e)}


async def describe_elastic_ips(
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
    """Analyze Elastic IPs for cost optimization.

    Unassociated EIPs cost ~$3.60/month ($0.005/hour).
    Returns a summary plus only the unassociated EIPs (actionable items).
    Associated EIPs are only counted in the summary.
    """
    try:
        ec2 = get_ec2_client(client_id, region)
        target_region = region or os.environ.get('AWS_REGION', 'eu-west-1')

        response = ec2.describe_addresses()

        total = 0
        associated = 0
        unassociated_eips = []

        for addr in response.get('Addresses', []):
            total += 1
            is_associated = addr.get('AssociationId') is not None

            if is_associated:
                associated += 1
            else:
                unassociated_eips.append({
                    'AllocationId': addr.get('AllocationId'),
                    'PublicIp': addr.get('PublicIp'),
                })

        unassociated = total - associated
        estimated_monthly_waste = round(unassociated * 0.005 * 24 * 30, 2)

        return {
            'summary': {
                'total': total,
                'associated': associated,
                'unassociated': unassociated,
                'estimated_monthly_waste_usd': estimated_monthly_waste,
            },
            'unassociated_eips': unassociated_eips,
            'region': target_region,
        }

    except Exception as e:
        logger.error(f'Error describing Elastic IPs: {e}')
        return {'error': str(e)}
