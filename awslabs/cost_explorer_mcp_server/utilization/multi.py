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

import os
from typing import Any, Dict, List, Optional

from loguru import logger
from mcp.server.fastmcp import Context
from pydantic import Field

from ..aws_clients import get_ec2_client, get_rds_client
from .ec2 import get_ec2_utilization
from .rds import get_rds_utilization


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
                        instance_info.append({
                            'id': instance.get('InstanceId'),
                            'type': instance.get('InstanceType'),
                            'lifecycle': instance.get('InstanceLifecycle', 'on-demand'),
                        })

            result['ec2']['total_instances'] = len(instance_info)

            # Get utilization for each (limit to avoid timeout)
            buckets: Dict[str, list] = {k: [] for k in BUCKET_LABELS}
            for info in instance_info:
                util = await get_ec2_utilization(
                    ctx, client_id, info['id'], resolved_region, days_back
                )

                if 'error' in util:
                    buckets['error'].append({
                        'instance_id': info['id'],
                        'instance_type': info['type'],
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
                        'instance_id': info['id'],
                        'instance_type': info['type'],
                        'lifecycle': info['lifecycle'],
                        'cpu_avg': cpu_avg,
                        'cpu_max': cpu_max,
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

            buckets: Dict[str, list] = {k: [] for k in BUCKET_LABELS}
            for info in db_info:
                util = await get_rds_utilization(
                    ctx, client_id, info['id'], resolved_region, days_back
                )

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
