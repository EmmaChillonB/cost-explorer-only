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
    """List all AWS regions and count of EC2 instances in each.
    
    Useful to discover which regions have EC2 instances before querying inventory.
    """
    try:
        # First get list of all regions
        ec2 = get_ec2_client(client_id, 'us-east-1')  # Any region works to list regions
        regions_response = ec2.describe_regions()
        
        regions_with_instances = []
        
        for region_info in regions_response.get('Regions', []):
            region_name = region_info['RegionName']
            try:
                regional_ec2 = get_ec2_client(client_id, region_name)
                # Quick count using describe_instances
                response = regional_ec2.describe_instances(MaxResults=5)
                
                # Count instances
                count = sum(
                    len(reservation.get('Instances', []))
                    for reservation in response.get('Reservations', [])
                )
                
                # Check if there might be more
                has_more = 'NextToken' in response
                
                if count > 0 or has_more:
                    regions_with_instances.append({
                        'region': region_name,
                        'instance_count': count if not has_more else f'{count}+',
                        'has_instances': True,
                    })
            except Exception as e:
                logger.debug(f'Error checking region {region_name}: {e}')
                continue
        
        return {
            'regions_with_instances': regions_with_instances,
            'total_regions_checked': len(regions_response.get('Regions', [])),
            'hint': 'Use describe_ec2_instances with region parameter to query specific region',
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
