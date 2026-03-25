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

"""EC2 inventory handler."""

import os
from typing import Any, Dict, List, Optional

from loguru import logger
from mcp.server.fastmcp import Context
from pydantic import Field

from ..aws_clients import get_ec2_client
from .common import serialize_datetime


async def list_ec2_regions_with_instances(
    ctx: Context,
    client_id: str = Field(
        ...,
        description="Client identifier to use for this request."
    ),
) -> Dict[str, Any]:
    """List AWS regions that have EC2 instances with a summary per region.

    Returns regions with instances including counts by state (running/stopped)
    and a breakdown of instance types found.
    """
    try:
        ec2 = get_ec2_client(client_id, 'us-east-1')
        regions_response = ec2.describe_regions()

        regions_with_instances = []

        for region_info in regions_response.get('Regions', []):
            region_name = region_info['RegionName']
            try:
                regional_ec2 = get_ec2_client(client_id, region_name)
                paginator = regional_ec2.get_paginator('describe_instances')

                running = 0
                stopped = 0
                other = 0
                instance_types: Dict[str, int] = {}

                for page in paginator.paginate():
                    for reservation in page.get('Reservations', []):
                        for inst in reservation.get('Instances', []):
                            state = inst.get('State', {}).get('Name', 'unknown')
                            if state == 'running':
                                running += 1
                            elif state == 'stopped':
                                stopped += 1
                            else:
                                other += 1
                            itype = inst.get('InstanceType', 'unknown')
                            instance_types[itype] = instance_types.get(itype, 0) + 1

                total = running + stopped + other
                if total > 0:
                    sorted_types = sorted(
                        instance_types.items(), key=lambda x: x[1], reverse=True
                    )
                    regions_with_instances.append({
                        'region': region_name,
                        'total': total,
                        'running': running,
                        'stopped': stopped,
                        'instance_types': [
                            {'type': t, 'count': c} for t, c in sorted_types
                        ],
                    })
            except Exception as e:
                logger.debug(f'Error checking region {region_name}: {e}')
                continue

        total_instances = sum(r['total'] for r in regions_with_instances)
        total_running = sum(r['running'] for r in regions_with_instances)
        total_stopped = sum(r['stopped'] for r in regions_with_instances)

        return {
            'regions': regions_with_instances,
            'summary': {
                'regions_with_instances': len(regions_with_instances),
                'total_instances': total_instances,
                'total_running': total_running,
                'total_stopped': total_stopped,
            },
        }

    except Exception as e:
        logger.error(f'Error listing regions with instances: {e}')
        return {'error': str(e)}


async def describe_ec2_instances(
    ctx: Context,
    client_id: str = Field(
        ...,
        description="Client identifier to use for this request."
    ),
    region: Optional[str] = Field(
        None,
        description="AWS region to query."
    ),
    instance_ids: Optional[List[str]] = Field(
        None,
        description="List of specific instance IDs to describe."
    ),
    filters: Optional[List[Dict[str, Any]]] = Field(
        None,
        description="List of filters. Example: [{'Name': 'instance-state-name', 'Values': ['running']}]"
    ),
    include_costs_optimization_info: bool = Field(
        True,
        description="Include additional information useful for cost optimization."
    ),
) -> Dict[str, Any]:
    """Describe EC2 instances with detailed information for cost optimization.

    Returns information about EC2 instances including instance type, state, 
    platform, availability zone, and tags.
    """
    try:
        target_region = region or os.environ.get('AWS_REGION', 'eu-west-1')
        logger.info(f'Querying EC2 instances for client {client_id} in region {target_region}')
        
        ec2 = get_ec2_client(client_id, region)
        
        params = {}
        # Only add parameters if they are actual list values (not None or FieldInfo)
        if instance_ids is not None and isinstance(instance_ids, list):
            params['InstanceIds'] = instance_ids
        if filters is not None and isinstance(filters, list):
            params['Filters'] = filters
        
        logger.debug(f'EC2 describe params: {params}')
        
        instances = []
        paginator = ec2.get_paginator('describe_instances')
        
        page_count = 0
        for page in paginator.paginate(**params):
            page_count += 1
            reservations = page.get('Reservations', [])
            logger.debug(f'Page {page_count}: {len(reservations)} reservations')
            for reservation in reservations:
                for instance in reservation.get('Instances', []):
                    if include_costs_optimization_info:
                        instance_info = {
                            'InstanceId': instance.get('InstanceId'),
                            'InstanceType': instance.get('InstanceType'),
                            'State': instance.get('State', {}).get('Name'),
                            'LaunchTime': instance.get('LaunchTime'),
                            'Platform': instance.get('Platform', 'linux'),
                            'AvailabilityZone': instance.get('Placement', {}).get('AvailabilityZone'),
                            'PrivateIpAddress': instance.get('PrivateIpAddress'),
                            'PublicIpAddress': instance.get('PublicIpAddress'),
                            'VpcId': instance.get('VpcId'),
                            'SubnetId': instance.get('SubnetId'),
                            'Architecture': instance.get('Architecture'),
                            'RootDeviceType': instance.get('RootDeviceType'),
                            'Tags': {tag['Key']: tag['Value'] for tag in instance.get('Tags', [])},
                            'EbsOptimized': instance.get('EbsOptimized'),
                            'InstanceLifecycle': instance.get('InstanceLifecycle', 'on-demand'),
                            'SpotInstanceRequestId': instance.get('SpotInstanceRequestId'),
                            'BlockDeviceMappings': [
                                {
                                    'DeviceName': bdm.get('DeviceName'),
                                    'VolumeId': bdm.get('Ebs', {}).get('VolumeId'),
                                    'DeleteOnTermination': bdm.get('Ebs', {}).get('DeleteOnTermination'),
                                }
                                for bdm in instance.get('BlockDeviceMappings', [])
                            ],
                        }
                    else:
                        instance_info = instance
                    
                    instances.append(serialize_datetime(instance_info))
        
        logger.info(f'Found {len(instances)} EC2 instances in {target_region}')
        
        return {
            'instances': instances,
            'count': len(instances),
            'region': target_region,
        }
        
    except Exception as e:
        logger.error(f'Error describing EC2 instances: {e}')
        return {'error': str(e)}
