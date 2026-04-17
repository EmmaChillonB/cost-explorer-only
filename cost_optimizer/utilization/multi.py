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

"""Multi-resource utilization handler."""

import asyncio
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from loguru import logger
from mcp.server.fastmcp import Context
from pydantic import Field

from ..aws_clients import get_ec2_client, get_rds_client
from .ec2 import get_ec2_utilization
from .rds import get_rds_utilization

# Max concurrent CloudWatch calls per tool invocation.
# CloudWatch allows ~400 req/s per account; 20 in-flight gives good
# throughput while staying well under the rate limit.
_MAX_CONCURRENT = 20


def _classify_cpu(cpu_avg: Optional[float]) -> str:
    """Classify CPU utilization into a bucket."""
    if cpu_avg is None:
        return 'unknown'
    if cpu_avg < 5:
        return 'critically_low'  # < 5%
    if cpu_avg < 20:
        return 'underutilized'  # 5-20%
    if cpu_avg < 50:
        return 'moderate'  # 20-50%
    if cpu_avg < 80:
        return 'healthy'  # 50-80%
    return 'high'  # > 80%


BUCKET_LABELS = {
    'critically_low': '< 5% (candidate for termination/downsize)',
    'underutilized': '5-20% (candidate for downsize)',
    'moderate': '20-50% (review sizing)',
    'healthy': '50-80% (well sized)',
    'high': '> 80% (consider upsize)',
    'unknown': 'No metrics available',
    'error': 'Error retrieving metrics',
}


async def get_multi_resource_utilization(
    ctx: Context,
    client_id: str = Field(
        ...,
        description="Client identifier to use for this request."
    ),
    region: Optional[str] = Field(
        None,
        description="AWS region."
    ),
    days_back: int = Field(
        7,
        description="Number of days to look back for metrics."
    ),
    include_ec2: bool = Field(
        True,
        description="Include EC2 instance utilization."
    ),
    include_rds: bool = Field(
        True,
        description="Include RDS database utilization."
    ),
    ec2_filters: Optional[List[Dict[str, Any]]] = Field(
        None,
        description="Filters for EC2 instances."
    ),
) -> Dict[str, Any]:
    """Get utilization metrics for multiple resources grouped by utilization buckets.

    Instead of listing every instance individually, this tool groups resources
    into utilization buckets (< 5%, 5-20%, 20-50%, 50-80%, > 80%) so you can
    quickly see the distribution and focus on the problematic ones.

    Each bucket contains the list of instance IDs and their CPU average.
    """
    try:
        resolved_region = region or os.environ.get('AWS_REGION', 'eu-west-1')

        result = {
            'region': resolved_region,
            'days_back': days_back,
            'ec2': {},
            'rds': {},
        }

        if include_ec2:
            ec2_client = get_ec2_client(client_id, resolved_region)
            filters = ec2_filters or [{'Name': 'instance-state-name', 'Values': ['running']}]

            # Collect instance IDs and types
            instance_info = []
            paginator = ec2_client.get_paginator('describe_instances')
            for page in paginator.paginate(Filters=filters):
                for reservation in page.get('Reservations', []):
                    for instance in reservation.get('Instances', []):
                        name = next(
                            (t['Value'] for t in instance.get('Tags', []) if t['Key'] == 'Name'),
                            None,
                        )
                        # Calculate uptime from LaunchTime
                        launch_time = instance.get('LaunchTime')
                        uptime_days = None
                        if launch_time:
                            if launch_time.tzinfo is None:
                                launch_time = launch_time.replace(tzinfo=timezone.utc)
                            uptime_days = (datetime.now(timezone.utc) - launch_time).days

                        instance_info.append({
                            'id': instance.get('InstanceId'),
                            'type': instance.get('InstanceType'),
                            'name': name,
                            'lifecycle': instance.get('InstanceLifecycle', 'on-demand'),
                            'state': instance.get('State', {}).get('Name', 'unknown'),
                            'uptime_days': uptime_days,
                        })

            result['ec2']['total_instances'] = len(instance_info)

            # Get utilization for each instance in parallel.
            # get_ec2_utilization is async on signature but uses sync boto3
            # internally, which blocks the event loop. We wrap each call with
            # asyncio.to_thread + a fresh event loop so they truly run in
            # parallel across worker threads.
            def _sync_ec2_util(info):
                return asyncio.new_event_loop().run_until_complete(
                    get_ec2_utilization(
                        ctx, client_id, info['id'], resolved_region, days_back
                    )
                )

            ec2_sem = asyncio.Semaphore(_MAX_CONCURRENT)

            async def _fetch_ec2(info):
                async with ec2_sem:
                    return await asyncio.to_thread(_sync_ec2_util, info)

            ec2_utils = await asyncio.gather(
                *[_fetch_ec2(info) for info in instance_info],
                return_exceptions=True,
            )

            buckets: Dict[str, list] = {k: [] for k in BUCKET_LABELS}
            for info, util in zip(instance_info, ec2_utils):
                if isinstance(util, Exception):
                    util = {'error': str(util)}

                if 'error' in util:
                    buckets['error'].append({
                        'instance_id': info['id'],
                        'instance_type': info['type'],
                        'name': info.get('name'),
                        'error': util.get('error'),
                    })
                else:
                    metrics = util.get('metrics', {})
                    cpu_avg = metrics.get('cpu', {}).get('summary', {}).get('overall_average')
                    cpu_max = metrics.get('cpu', {}).get('summary', {}).get('overall_maximum')

                    # Network (bytes → MB/day for readability)
                    net_in_sum = metrics.get('network_in', {}).get('summary', {}).get('overall_sum')
                    net_out_sum = metrics.get('network_out', {}).get('summary', {}).get('overall_sum')
                    net_in_mb_day = round(net_in_sum / (1024 * 1024) / days_back, 1) if net_in_sum else None
                    net_out_mb_day = round(net_out_sum / (1024 * 1024) / days_back, 1) if net_out_sum else None

                    bucket = _classify_cpu(cpu_avg)
                    buckets[bucket].append({
                        'instance_id': info['id'],
                        'instance_type': info['type'],
                        'name': info.get('name'),
                        'lifecycle': info['lifecycle'],
                        'state': info.get('state', 'unknown'),
                        'uptime_days': info.get('uptime_days'),
                        'cpu_avg': cpu_avg,
                        'cpu_max': cpu_max,
                        'net_in_mb_day': net_in_mb_day,
                        'net_out_mb_day': net_out_mb_day,
                    })

            # Build compact output - only include non-empty buckets
            ec2_buckets = {}
            for bucket_key, instances in buckets.items():
                if instances:
                    ec2_buckets[bucket_key] = {
                        'label': BUCKET_LABELS[bucket_key],
                        'count': len(instances),
                        'instances': instances,
                    }
            result['ec2']['utilization_buckets'] = ec2_buckets

        if include_rds:
            rds_client = get_rds_client(client_id, resolved_region)

            db_info = []
            paginator = rds_client.get_paginator('describe_db_instances')
            for page in paginator.paginate():
                for db in page.get('DBInstances', []):
                    if db.get('DBInstanceStatus') == 'available':
                        db_info.append({
                            'id': db.get('DBInstanceIdentifier'),
                            'class': db.get('DBInstanceClass'),
                            'engine': db.get('Engine'),
                            'multi_az': db.get('MultiAZ', False),
                        })

            result['rds']['total_instances'] = len(db_info)

            # Same pattern as EC2: wrap async-but-actually-sync calls with
            # asyncio.to_thread so they run in worker threads in parallel.
            def _sync_rds_util(info):
                return asyncio.new_event_loop().run_until_complete(
                    get_rds_utilization(
                        ctx, client_id, info['id'], resolved_region, days_back
                    )
                )

            rds_sem = asyncio.Semaphore(_MAX_CONCURRENT)

            async def _fetch_rds(info):
                async with rds_sem:
                    return await asyncio.to_thread(_sync_rds_util, info)

            rds_utils = await asyncio.gather(
                *[_fetch_rds(info) for info in db_info],
                return_exceptions=True,
            )

            buckets: Dict[str, list] = {k: [] for k in BUCKET_LABELS}
            for info, util in zip(db_info, rds_utils):
                if isinstance(util, Exception):
                    util = {'error': str(util)}

                if 'error' in util:
                    buckets['error'].append({
                        'db_identifier': info['id'],
                        'db_class': info['class'],
                        'engine': info['engine'],
                        'error': util.get('error'),
                    })
                else:
                    cpu_avg = (
                        util.get('metrics', {}).get('cpu', {})
                        .get('summary', {}).get('overall_average')
                    )
                    cpu_max = (
                        util.get('metrics', {}).get('cpu', {})
                        .get('summary', {}).get('overall_maximum')
                    )
                    bucket = _classify_cpu(cpu_avg)
                    buckets[bucket].append({
                        'db_identifier': info['id'],
                        'db_class': info['class'],
                        'engine': info['engine'],
                        'multi_az': info['multi_az'],
                        'cpu_avg': cpu_avg,
                        'cpu_max': cpu_max,
                    })

            rds_buckets = {}
            for bucket_key, instances in buckets.items():
                if instances:
                    rds_buckets[bucket_key] = {
                        'label': BUCKET_LABELS[bucket_key],
                        'count': len(instances),
                        'instances': instances,
                    }
            result['rds']['utilization_buckets'] = rds_buckets

        return result

    except Exception as e:
        logger.error(f'Error getting multi-resource utilization: {e}')
        return {'error': str(e)}
