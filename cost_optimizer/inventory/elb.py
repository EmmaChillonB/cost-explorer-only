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
(no healthy targets, idle LBs). Scans all enabled regions in parallel
when no region is given.
"""

import asyncio
from typing import Any, Dict, List, Optional

from loguru import logger
from mcp.server.fastmcp import Context
from pydantic import Field

from ..aws_clients import get_elbv2_client
from .common import (
    REGIONS_MAX_CONCURRENT,
    list_enabled_regions,
    serialize_datetime,
)


def _describe_load_balancers_region_sync(
    client_id: str, region: str
) -> Dict[str, Any]:
    """Scan Load Balancers in a single region. Sync — runs in a worker thread."""
    elbv2 = get_elbv2_client(client_id, region)

    paginator = elbv2.get_paginator('describe_load_balancers')

    total = 0
    by_type: Dict[str, int] = {}
    lbs_no_targets: List[Dict[str, Any]] = []
    lbs_all_unhealthy: List[Dict[str, Any]] = []

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
                'Region': region,
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
        'region': region,
        'total': total,
        'by_type': by_type,
        'lbs_no_targets': lbs_no_targets,
        'lbs_all_unhealthy': lbs_all_unhealthy,
    }


async def describe_load_balancers(
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
    """Analyze Load Balancers for cost optimization across one or all regions.

    Returns a summary with count by type, plus detail only for LBs
    with no healthy targets (candidates for removal/investigation).
    Well-functioning LBs are only counted in the summary. Each flagged
    entry includes its ``Region`` so multi-region results are disambiguated.
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
                        _describe_load_balancers_region_sync, client_id, r
                    )
                except Exception as e:
                    logger.warning(f'Error describing load balancers in {r}: {e}')
                    return None

        results = await asyncio.gather(*[_scan(r) for r in regions_to_scan])
        results = [r for r in results if r is not None]

        total = sum(r['total'] for r in results)
        by_type: Dict[str, int] = {}
        lbs_no_targets: List[Dict[str, Any]] = []
        lbs_all_unhealthy: List[Dict[str, Any]] = []
        regions_with_resources: List[str] = []
        for r in results:
            for t, c in r['by_type'].items():
                by_type[t] = by_type.get(t, 0) + c
            lbs_no_targets.extend(r['lbs_no_targets'])
            lbs_all_unhealthy.extend(r['lbs_all_unhealthy'])
            if r['total'] > 0:
                regions_with_resources.append(r['region'])

        return {
            'summary': {
                'total': total,
                'by_type': by_type,
                'with_no_targets': len(lbs_no_targets),
                'with_all_unhealthy': len(lbs_all_unhealthy),
                'regions_scanned': len(regions_to_scan),
                'regions_with_resources': regions_with_resources,
            },
            'lbs_no_targets': lbs_no_targets,
            'lbs_all_unhealthy': lbs_all_unhealthy,
        }

    except Exception as e:
        logger.error(f'Error describing load balancers: {e}')
        return {'error': str(e)}
