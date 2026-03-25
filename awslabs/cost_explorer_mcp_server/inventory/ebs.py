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

"""EBS inventory handler — returns pre-analyzed data for cost optimization reports.

Only actionable items are returned in detail (unattached volumes, gp2 candidates,
orphaned/old snapshots). Healthy resources are counted in the summary only.
"""

import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from loguru import logger
from mcp.server.fastmcp import Context
from pydantic import Field

from ..aws_clients import get_ec2_client
from .common import serialize_datetime

# Snapshots older than this are flagged for review
SNAPSHOT_AGE_THRESHOLD_DAYS = 90


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
) -> Dict[str, Any]:
    """Analyze EBS volumes and return only actionable findings for cost optimization.

    Returns a summary of all volumes plus detail lists for:
    - Unattached volumes (candidates for deletion)
    - gp2 volumes (candidates for gp3 migration)
    """
    try:
        ec2 = get_ec2_client(client_id, region)
        target_region = region or os.environ.get('AWS_REGION', 'eu-west-1')

        paginator = ec2.get_paginator('describe_volumes')

        total = 0
        attached = 0
        total_size_gb = 0
        unattached_size_gb = 0
        type_counts: Dict[str, int] = {}
        type_size_gb: Dict[str, int] = {}
        unattached_volumes = []
        gp2_volumes = []

        for page in paginator.paginate():
            for volume in page.get('Volumes', []):
                total += 1
                size = volume.get('Size', 0)
                vtype = volume.get('VolumeType', 'unknown')
                total_size_gb += size
                type_counts[vtype] = type_counts.get(vtype, 0) + 1
                type_size_gb[vtype] = type_size_gb.get(vtype, 0) + size

                attachments = volume.get('Attachments', [])
                is_attached = len(attachments) > 0

                if is_attached:
                    attached += 1
                else:
                    unattached_size_gb += size
                    unattached_volumes.append(serialize_datetime({
                        'VolumeId': volume.get('VolumeId'),
                        'SizeGB': size,
                        'VolumeType': vtype,
                        'CreateTime': volume.get('CreateTime'),
                    }))

                if vtype == 'gp2' and is_attached:
                    att = attachments[0]
                    gp2_volumes.append({
                        'VolumeId': volume.get('VolumeId'),
                        'SizeGB': size,
                        'AttachedTo': att.get('InstanceId'),
                        'Iops': volume.get('Iops'),
                    })

        unattached = total - attached

        return {
            'summary': {
                'total': total,
                'attached': attached,
                'unattached': unattached,
                'total_size_gb': total_size_gb,
                'unattached_size_gb': unattached_size_gb,
                'by_type': {
                    t: {'count': type_counts[t], 'size_gb': type_size_gb[t]}
                    for t in sorted(type_counts, key=lambda k: type_size_gb[k], reverse=True)
                },
            },
            'unattached_volumes': unattached_volumes,
            'gp2_migration_candidates': gp2_volumes,
            'region': target_region,
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
) -> Dict[str, Any]:
    """Analyze EBS snapshots and return only actionable findings for cost optimization.

    Returns a summary of all snapshots plus detail lists for:
    - Orphaned snapshots (source volume deleted)
    - Old snapshots (> 90 days) that may be candidates for cleanup
    """
    try:
        ec2 = get_ec2_client(client_id, region)
        target_region = region or os.environ.get('AWS_REGION', 'eu-west-1')
        now = datetime.now(timezone.utc)

        # Get current volume IDs to detect orphans
        current_volume_ids = set()
        vol_paginator = ec2.get_paginator('describe_volumes')
        for page in vol_paginator.paginate():
            for volume in page.get('Volumes', []):
                current_volume_ids.add(volume.get('VolumeId'))

        paginator = ec2.get_paginator('describe_snapshots')

        total = 0
        total_size_gb = 0
        orphaned_count = 0
        orphaned_size_gb = 0
        old_count = 0
        old_size_gb = 0
        orphaned_snapshots = []
        old_snapshots = []

        for page in paginator.paginate(OwnerIds=['self']):
            for snap in page.get('Snapshots', []):
                total += 1
                size = snap.get('VolumeSize', 0)
                total_size_gb += size
                volume_id = snap.get('VolumeId')
                start_time = snap.get('StartTime')

                is_orphaned = bool(volume_id and volume_id not in current_volume_ids)
                age_days = None
                if start_time:
                    if start_time.tzinfo is None:
                        start_time = start_time.replace(tzinfo=timezone.utc)
                    age_days = (now - start_time).days

                if is_orphaned:
                    orphaned_count += 1
                    orphaned_size_gb += size
                    orphaned_snapshots.append(serialize_datetime({
                        'SnapshotId': snap.get('SnapshotId'),
                        'VolumeId': volume_id,
                        'SizeGB': size,
                        'AgeDays': age_days,
                    }))

                if age_days and age_days > SNAPSHOT_AGE_THRESHOLD_DAYS and not is_orphaned:
                    old_count += 1
                    old_size_gb += size
                    old_snapshots.append(serialize_datetime({
                        'SnapshotId': snap.get('SnapshotId'),
                        'VolumeId': volume_id,
                        'SizeGB': size,
                        'AgeDays': age_days,
                    }))

        # Limit lists to top by size to avoid huge responses
        orphaned_snapshots.sort(key=lambda x: x.get('SizeGB', 0), reverse=True)
        old_snapshots.sort(key=lambda x: x.get('SizeGB', 0), reverse=True)

        return {
            'summary': {
                'total': total,
                'total_size_gb': total_size_gb,
                'orphaned': orphaned_count,
                'orphaned_size_gb': orphaned_size_gb,
                'older_than_90d': old_count,
                'older_than_90d_size_gb': old_size_gb,
            },
            'orphaned_snapshots': orphaned_snapshots[:20],
            'old_snapshots': old_snapshots[:20],
            'region': target_region,
        }

    except Exception as e:
        logger.error(f'Error describing EBS snapshots: {e}')
        return {'error': str(e)}
