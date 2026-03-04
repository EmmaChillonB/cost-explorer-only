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

"""Network resources inventory handler (NAT Gateways, Elastic IPs)."""

import os
from typing import Any, Dict, List, Optional

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
    nat_gateway_ids: Optional[List[str]] = Field(
        None,
        description="List of specific NAT Gateway IDs to describe."
    ),
    filters: Optional[List[Dict[str, Any]]] = Field(
        None,
        description="List of filters to apply."
    ),
) -> Dict[str, Any]:
    """Describe NAT Gateways with cost-relevant information.

    NAT Gateways are expensive ($0.045/hour + data processing charges).
    """
    try:
        ec2 = get_ec2_client(client_id, region)
        
        params = {}
        # Only add parameters if they are actual list values (not None or FieldInfo)
        if nat_gateway_ids is not None and isinstance(nat_gateway_ids, list):
            params['NatGatewayIds'] = nat_gateway_ids
        if filters is not None and isinstance(filters, list):
            params['Filter'] = filters
        
        nat_gateways = []
        paginator = ec2.get_paginator('describe_nat_gateways')
        
        for page in paginator.paginate(**params):
            for nat in page.get('NatGateways', []):
                nat_info = {
                    'NatGatewayId': nat.get('NatGatewayId'),
                    'State': nat.get('State'),
                    'VpcId': nat.get('VpcId'),
                    'SubnetId': nat.get('SubnetId'),
                    'ConnectivityType': nat.get('ConnectivityType'),
                    'CreateTime': nat.get('CreateTime'),
                    'NatGatewayAddresses': [
                        {
                            'AllocationId': addr.get('AllocationId'),
                            'PublicIp': addr.get('PublicIp'),
                            'PrivateIp': addr.get('PrivateIp'),
                            'NetworkInterfaceId': addr.get('NetworkInterfaceId'),
                        }
                        for addr in nat.get('NatGatewayAddresses', [])
                    ],
                    'Tags': {tag['Key']: tag['Value'] for tag in nat.get('Tags', [])},
                }
                nat_gateways.append(serialize_datetime(nat_info))
        
        active_count = sum(1 for n in nat_gateways if n['State'] == 'available')
        estimated_monthly_base_cost = active_count * 0.045 * 24 * 30
        
        return {
            'nat_gateways': nat_gateways,
            'count': len(nat_gateways),
            'active_count': active_count,
            'estimated_monthly_base_cost_usd': round(estimated_monthly_base_cost, 2),
            'region': region or os.environ.get('AWS_REGION', 'eu-west-1'),
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
    allocation_ids: Optional[List[str]] = Field(
        None,
        description="List of specific allocation IDs to describe."
    ),
    public_ips: Optional[List[str]] = Field(
        None,
        description="List of specific public IPs to describe."
    ),
) -> Dict[str, Any]:
    """Describe Elastic IPs with association status.

    Unassociated Elastic IPs incur charges ($0.005/hour = ~$3.60/month).
    """
    try:
        ec2 = get_ec2_client(client_id, region)
        
        params = {}
        # Only add parameters if they are actual list values (not None or FieldInfo)
        if allocation_ids is not None and isinstance(allocation_ids, list):
            params['AllocationIds'] = allocation_ids
        if public_ips is not None and isinstance(public_ips, list):
            params['PublicIps'] = public_ips
        
        response = ec2.describe_addresses(**params)
        
        elastic_ips = []
        for addr in response.get('Addresses', []):
            eip_info = {
                'AllocationId': addr.get('AllocationId'),
                'PublicIp': addr.get('PublicIp'),
                'AssociationId': addr.get('AssociationId'),
                'InstanceId': addr.get('InstanceId'),
                'NetworkInterfaceId': addr.get('NetworkInterfaceId'),
                'PrivateIpAddress': addr.get('PrivateIpAddress'),
                'Domain': addr.get('Domain'),
                'IsAssociated': addr.get('AssociationId') is not None,
                'Tags': {tag['Key']: tag['Value'] for tag in addr.get('Tags', [])},
            }
            elastic_ips.append(eip_info)
        
        unassociated_count = sum(1 for eip in elastic_ips if not eip['IsAssociated'])
        estimated_monthly_waste = unassociated_count * 0.005 * 24 * 30
        
        return {
            'elastic_ips': elastic_ips,
            'count': len(elastic_ips),
            'unassociated_count': unassociated_count,
            'estimated_monthly_waste_usd': round(estimated_monthly_waste, 2),
            'region': region or os.environ.get('AWS_REGION', 'eu-west-1'),
        }
        
    except Exception as e:
        logger.error(f'Error describing Elastic IPs: {e}')
        return {'error': str(e)}
