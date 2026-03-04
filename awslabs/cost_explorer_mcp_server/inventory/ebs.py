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

"""EBS inventory handler."""

import os
from typing import Any, Dict, List, Optional

from loguru import logger
from mcp.server.fastmcp import Context
from pydantic import Field

from ..aws_clients import get_ec2_client
from .common import serialize_datetime


async def describe_ebs_volumes(
    ctx: Context,
    client_id: str = Field(
        ...,
        description="Client identifier to use for this request."
    ),
    region: Optional[str] = Field(
        None,
        description="AWS region to query."
    ),
    volume_ids: Optional[List[str]] = Field(
        None,
        description="List of specific volume IDs to describe."
    ),
    filters: Optional[List[Dict[str, Any]]] = Field(
        None,
        description="List of filters. Example: [{'Name': 'status', 'Values': ['available']}]"
    ),
    include_unattached_only: bool = Field(
        False,
        description="If True, only return unattached volumes."
    ),
) -> Dict[str, Any]:
    """Describe EBS volumes with detailed information for cost optimization.

    Returns information about EBS volumes including size, type, state, and attachments.
    """
    try:
        ec2 = get_ec2_client(client_id, region)
        
        params = {}
        # Only add parameters if they are actual list values (not None or FieldInfo)
        if volume_ids is not None and isinstance(volume_ids, list):
            params['VolumeIds'] = volume_ids
        
        filter_list = filters if isinstance(filters, list) else []
        if include_unattached_only:
            filter_list.append({'Name': 'status', 'Values': ['available']})
        if filter_list:
            params['Filters'] = filter_list
        
        volumes = []
        paginator = ec2.get_paginator('describe_volumes')
        
        for page in paginator.paginate(**params):
            for volume in page.get('Volumes', []):
                volume_info = {
                    'VolumeId': volume.get('VolumeId'),
                    'Size': volume.get('Size'),
                    'VolumeType': volume.get('VolumeType'),
                    'State': volume.get('State'),
                    'AvailabilityZone': volume.get('AvailabilityZone'),
                    'CreateTime': volume.get('CreateTime'),
                    'Encrypted': volume.get('Encrypted'),
                    'Iops': volume.get('Iops'),
                    'Throughput': volume.get('Throughput'),
                    'SnapshotId': volume.get('SnapshotId'),
                    'Attachments': [
                        {
                            'InstanceId': att.get('InstanceId'),
                            'Device': att.get('Device'),
                            'State': att.get('State'),
                            'DeleteOnTermination': att.get('DeleteOnTermination'),
                        }
                        for att in volume.get('Attachments', [])
                    ],
                    'IsAttached': len(volume.get('Attachments', [])) > 0,
                    'Tags': {tag['Key']: tag['Value'] for tag in volume.get('Tags', [])},
                }
                volumes.append(serialize_datetime(volume_info))
        
        unattached_count = sum(1 for v in volumes if not v['IsAttached'])
        total_size_gb = sum(v['Size'] for v in volumes)
        unattached_size_gb = sum(v['Size'] for v in volumes if not v['IsAttached'])
        
        return {
            'volumes': volumes,
            'count': len(volumes),
            'unattached_count': unattached_count,
            'total_size_gb': total_size_gb,
            'unattached_size_gb': unattached_size_gb,
            'region': region or os.environ.get('AWS_REGION', 'eu-west-1'),
        }
        
    except Exception as e:
        logger.error(f'Error describing EBS volumes: {e}')
        return {'error': str(e)}


async def describe_ebs_snapshots(
    ctx: Context,
    client_id: str = Field(
        ...,
        description="Client identifier to use for this request."
    ),
    region: Optional[str] = Field(
        None,
        description="AWS region to query."
    ),
    owner_ids: Optional[List[str]] = Field(
        None,
        description="List of owner IDs. Use ['self'] for your account."
    ),
    snapshot_ids: Optional[List[str]] = Field(
        None,
        description="List of specific snapshot IDs to describe."
    ),
    include_orphaned_only: bool = Field(
        False,
        description="If True, only return orphaned snapshots."
    ),
) -> Dict[str, Any]:
    """Describe EBS snapshots with orphan detection for cost optimization.

    Returns information about EBS snapshots and identifies orphaned snapshots
    (snapshots whose source volume has been deleted).
    """
    try:
        ec2 = get_ec2_client(client_id, region)
        
        # Get all current volume IDs
        current_volume_ids = set()
        vol_paginator = ec2.get_paginator('describe_volumes')
        for page in vol_paginator.paginate():
            for volume in page.get('Volumes', []):
                current_volume_ids.add(volume.get('VolumeId'))
        
        # Get snapshots
        params = {}
        if owner_ids is not None and isinstance(owner_ids, list):
            params['OwnerIds'] = owner_ids
        else:
            params['OwnerIds'] = ['self']
        if snapshot_ids is not None and isinstance(snapshot_ids, list):
            params['SnapshotIds'] = snapshot_ids
        
        snapshots = []
        paginator = ec2.get_paginator('describe_snapshots')
        
        for page in paginator.paginate(**params):
            for snapshot in page.get('Snapshots', []):
                volume_id = snapshot.get('VolumeId')
                is_orphaned = volume_id and volume_id not in current_volume_ids
                
                if include_orphaned_only and not is_orphaned:
                    continue
                
                snapshot_info = {
                    'SnapshotId': snapshot.get('SnapshotId'),
                    'VolumeId': volume_id,
                    'VolumeSize': snapshot.get('VolumeSize'),
                    'State': snapshot.get('State'),
                    'StartTime': snapshot.get('StartTime'),
                    'Description': snapshot.get('Description'),
                    'Encrypted': snapshot.get('Encrypted'),
                    'IsOrphaned': is_orphaned,
                    'Tags': {tag['Key']: tag['Value'] for tag in snapshot.get('Tags', [])},
                }
                snapshots.append(serialize_datetime(snapshot_info))
        
        orphaned_count = sum(1 for s in snapshots if s['IsOrphaned'])
        total_size_gb = sum(s['VolumeSize'] for s in snapshots)
        orphaned_size_gb = sum(s['VolumeSize'] for s in snapshots if s['IsOrphaned'])
        
        return {
            'snapshots': snapshots,
            'count': len(snapshots),
            'orphaned_count': orphaned_count,
            'total_size_gb': total_size_gb,
            'orphaned_size_gb': orphaned_size_gb,
            'region': region or os.environ.get('AWS_REGION', 'eu-west-1'),
        }
        
    except Exception as e:
        logger.error(f'Error describing EBS snapshots: {e}')
        return {'error': str(e)}
