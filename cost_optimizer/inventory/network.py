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
Both handlers scan all enabled regions in parallel when no region is given.
"""

import asyncio
from typing import Any, Dict, List, Optional

from loguru import logger
from mcp.server.fastmcp import Context
from pydantic import Field

from ..aws_clients import get_ec2_client
from .common import (
    REGIONS_MAX_CONCURRENT,
    list_enabled_regions,
    serialize_datetime,
)


def _describe_nat_gateways_region_sync(
    client_id: str, region: str
) -> Dict[str, Any]:
    """Scan NAT Gateways in a single region. Sync — runs in a worker thread."""
    ec2 = get_ec2_client(client_id, region)
    paginator = ec2.get_paginator('describe_nat_gateways')

    active = 0
    total = 0
    nat_list: List[Dict[str, Any]] = []
    vpc_counts: Dict[str, int] = {}

    for page in paginator.paginate():
        for nat in page.get('NatGateways', []):
            total += 1
            state = nat.get('State')
            vpc_id = nat.get('VpcId', 'unknown')

            if state == 'available':
                active += 1
                # Namespace the VPC key by region to avoid collisions across regions.
                key = f'{region}:{vpc_id}'
                vpc_counts[key] = vpc_counts.get(key, 0) + 1

            nat_list.append(serialize_datetime({
                'NatGatewayId': nat.get('NatGatewayId'),
                'Region': region,
                'State': state,
                'VpcId': vpc_id,
                'SubnetId': nat.get('SubnetId'),
                'ConnectivityType': nat.get('ConnectivityType'),
                'CreateTime': nat.get('CreateTime'),
            }))

    return {
        'region': region,
        'total': total,
        'active': active,
        'nat_list': nat_list,
        'vpc_counts': vpc_counts,
    }


async def describe_nat_gateways(
    ctx: Context,
    client_id: str = Field(
        ...,
        description="Client identifier to use for this request."
    ),
    region: Optional[str] = Field(
        None,
        description="AWS region to query. If omitted, scans all enabled regions in parallel."
    ),
) -> Dict[str, Any]:
    """Analyze NAT Gateways for cost optimization across one or all regions.

    NAT Gateways cost ~$32.40/month each ($0.045/hour) plus data processing.
    Returns a summary with count and cost estimate, plus a compact list
    with only the fields relevant for optimization decisions. Each entry
    includes its ``Region`` so multi-region results are disambiguated.
    """
    try:
        if region:
            regions_to_scan = [region]
        else:
            regions_to_scan = await asyncio.to_thread(list_enabled_regions, client_id)

        sem = asyncio.Semaphore(REGIONS_MAX_CONCURRENT)

        async def _scan(r: str) -> Optional[Dict[str, Any]]:
            async with sem:
                try:
                    return await asyncio.to_thread(
                        _describe_nat_gateways_region_sync, client_id, r
                    )
                except Exception as e:
                    logger.warning(f'Error describing NAT Gateways in {r}: {e}')
                    return None

        results = await asyncio.gather(*[_scan(r) for r in regions_to_scan])
        results = [r for r in results if r is not None]

        total = sum(r['total'] for r in results)
        active = sum(r['active'] for r in results)
        nat_list: List[Dict[str, Any]] = []
        vpc_counts: Dict[str, int] = {}
        regions_with_resources: List[str] = []
        for r in results:
            nat_list.extend(r['nat_list'])
            vpc_counts.update(r['vpc_counts'])
            if r['total'] > 0:
                regions_with_resources.append(r['region'])

        estimated_monthly = round(active * 0.045 * 24 * 30, 2)
        vpcs_with_multiple = {
            vpc: count for vpc, count in vpc_counts.items() if count > 1
        }

        return {
            'summary': {
                'total': total,
                'active': active,
                'estimated_monthly_base_cost_usd': estimated_monthly,
                'vpcs_with_multiple_nat_gws': vpcs_with_multiple,
                'regions_scanned': len(regions_to_scan),
                'regions_with_resources': regions_with_resources,
            },
            'nat_gateways': nat_list,
        }

    except Exception as e:
        logger.error(f'Error describing NAT Gateways: {e}')
        return {'error': str(e)}


def _describe_elastic_ips_region_sync(
    client_id: str, region: str
) -> Dict[str, Any]:
    """Scan Elastic IPs in a single region. Sync — runs in a worker thread."""
    ec2 = get_ec2_client(client_id, region)

    response = ec2.describe_addresses()

    total = 0
    associated = 0
    unassociated_eips: List[Dict[str, Any]] = []

    for addr in response.get('Addresses', []):
        total += 1
        is_associated = addr.get('AssociationId') is not None

        if is_associated:
            associated += 1
        else:
            unassociated_eips.append({
                'AllocationId': addr.get('AllocationId'),
                'PublicIp': addr.get('PublicIp'),
                'Region': region,
            })

    return {
        'region': region,
        'total': total,
        'associated': associated,
        'unassociated_eips': unassociated_eips,
    }


async def describe_elastic_ips(
    ctx: Context,
    client_id: str = Field(
        ...,
        description="Client identifier to use for this request."
    ),
    region: Optional[str] = Field(
        None,
        description="AWS region to query. If omitted, scans all enabled regions in parallel."
    ),
) -> Dict[str, Any]:
    """Analyze Elastic IPs for cost optimization across one or all regions.

    Unassociated EIPs cost ~$3.60/month ($0.005/hour).
    Returns a summary plus only the unassociated EIPs (actionable items).
    Associated EIPs are only counted in the summary.
    """
    try:
        if region:
            regions_to_scan = [region]
        else:
            regions_to_scan = await asyncio.to_thread(list_enabled_regions, client_id)

        sem = asyncio.Semaphore(REGIONS_MAX_CONCURRENT)

        async def _scan(r: str) -> Optional[Dict[str, Any]]:
            async with sem:
                try:
                    return await asyncio.to_thread(
                        _describe_elastic_ips_region_sync, client_id, r
                    )
                except Exception as e:
                    logger.warning(f'Error describing Elastic IPs in {r}: {e}')
                    return None

        results = await asyncio.gather(*[_scan(r) for r in regions_to_scan])
        results = [r for r in results if r is not None]

        total = sum(r['total'] for r in results)
        associated = sum(r['associated'] for r in results)
        unassociated_eips: List[Dict[str, Any]] = []
        regions_with_resources: List[str] = []
        for r in results:
            unassociated_eips.extend(r['unassociated_eips'])
            if r['total'] > 0:
                regions_with_resources.append(r['region'])

        unassociated = total - associated
        estimated_monthly_waste = round(unassociated * 0.005 * 24 * 30, 2)

        return {
            'summary': {
                'total': total,
                'associated': associated,
                'unassociated': unassociated,
                'estimated_monthly_waste_usd': estimated_monthly_waste,
                'regions_scanned': len(regions_to_scan),
                'regions_with_resources': regions_with_resources,
            },
            'unassociated_eips': unassociated_eips,
        }

    except Exception as e:
        logger.error(f'Error describing Elastic IPs: {e}')
        return {'error': str(e)}
