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

"""RDS inventory handler."""

import asyncio
import os
from typing import Any, Dict, List, Optional

from botocore.exceptions import ClientError
from loguru import logger
from mcp.server.fastmcp import Context
from pydantic import Field

from ..aws_clients import get_rds_client
from .common import serialize_datetime


_MAX_RECS_PER_DB = 3
_ACTIVE_STATUSES = {'active', 'pending'}


async def describe_rds_instances(
    ctx: Context,
    client_id: str = Field(
        ...,
        description="Client identifier to use for this request."
    ),
    region: Optional[str] = Field(
        None,
        description="AWS region to query."
    ),
    db_instance_identifier: Optional[str] = Field(
        None,
        description="Specific DB instance identifier to describe."
    ),
    include_costs_optimization_info: bool = Field(
        True,
        description="Include additional information useful for cost optimization."
    ),
) -> Dict[str, Any]:
    """Describe RDS database instances with detailed information for cost optimization.

    Returns information about RDS instances including instance class, engine,
    storage, multi-AZ configuration, and more.
    """
    try:
        rds = get_rds_client(client_id, region)
        
        params = {}
        if db_instance_identifier:
            params['DBInstanceIdentifier'] = db_instance_identifier
        
        instances = []
        paginator = rds.get_paginator('describe_db_instances')
        
        for page in paginator.paginate(**params):
            for db_instance in page.get('DBInstances', []):
                if include_costs_optimization_info:
                    instance_info = {
                        'DBInstanceIdentifier': db_instance.get('DBInstanceIdentifier'),
                        'DBInstanceClass': db_instance.get('DBInstanceClass'),
                        'Engine': db_instance.get('Engine'),
                        'EngineVersion': db_instance.get('EngineVersion'),
                        'DBInstanceStatus': db_instance.get('DBInstanceStatus'),
                        'AllocatedStorage': db_instance.get('AllocatedStorage'),
                        'StorageType': db_instance.get('StorageType'),
                        'Iops': db_instance.get('Iops'),
                        'MultiAZ': db_instance.get('MultiAZ'),
                        'AvailabilityZone': db_instance.get('AvailabilityZone'),
                        'PubliclyAccessible': db_instance.get('PubliclyAccessible'),
                        'StorageEncrypted': db_instance.get('StorageEncrypted'),
                        'InstanceCreateTime': db_instance.get('InstanceCreateTime'),
                        'BackupRetentionPeriod': db_instance.get('BackupRetentionPeriod'),
                        'AutoMinorVersionUpgrade': db_instance.get('AutoMinorVersionUpgrade'),
                        'LicenseModel': db_instance.get('LicenseModel'),
                        'DeletionProtection': db_instance.get('DeletionProtection'),
                        'PerformanceInsightsEnabled': db_instance.get('PerformanceInsightsEnabled'),
                        'Tags': {tag['Key']: tag['Value'] for tag in db_instance.get('TagList', [])},
                    }
                else:
                    instance_info = db_instance
                
                instances.append(serialize_datetime(instance_info))
        
        return {
            'db_instances': instances,
            'count': len(instances),
            'region': region or os.environ.get('AWS_REGION', 'eu-west-1'),
        }
        
    except Exception as e:
        logger.error(f'Error describing RDS instances: {e}')
        return {'error': str(e)}


def _arn_to_db_identifier(arn: str) -> Optional[str]:
    """Extract DB identifier from an RDS ARN (arn:aws:rds:region:acct:db:identifier)."""
    if not arn:
        return None
    parts = arn.split(':')
    if len(parts) >= 7 and parts[5] == 'db':
        return parts[6]
    return None


def _compact_recommendation(rec: Dict[str, Any]) -> Dict[str, Any]:
    """Strip an RDS recommendation to the fields the agent needs."""
    return {
        'severity': rec.get('Severity'),
        'category': rec.get('Category'),
        'recommendation': rec.get('Recommendation'),
        'impact': rec.get('Impact'),
        'reason': rec.get('Reason'),
    }


def _fetch_rds_recommendations_sync(
    client_id: str,
    region: Optional[str],
    db_instance_identifiers: Optional[List[str]],
) -> Dict[str, Any]:
    """Blocking worker — calls describe_db_recommendations and compacts the output."""
    rds = get_rds_client(client_id, region)

    filters = [{'Name': 'status', 'Values': list(_ACTIVE_STATUSES)}]
    by_db: Dict[str, List[Dict[str, Any]]] = {}

    try:
        paginator = rds.get_paginator('describe_db_recommendations')
        for page in paginator.paginate(Filters=filters):
            for rec in page.get('DBRecommendations', []):
                db_id = _arn_to_db_identifier(rec.get('ResourceArn', ''))
                if not db_id:
                    continue
                if db_instance_identifiers and db_id not in db_instance_identifiers:
                    continue
                bucket = by_db.setdefault(db_id, [])
                if len(bucket) < _MAX_RECS_PER_DB:
                    bucket.append(_compact_recommendation(rec))
    except ClientError as e:
        code = e.response.get('Error', {}).get('Code', '')
        # Older boto3 versions or restricted accounts may not expose this API
        if code in ('InvalidAction', 'UnrecognizedClientException', 'AccessDenied'):
            logger.warning(f'describe_db_recommendations unavailable: {code}')
            return {
                'region': region or os.environ.get('AWS_REGION', 'eu-west-1'),
                'recommendations_by_db': {},
                '_warning': (
                    f'AWS RDS recommendations API unavailable in this region/account '
                    f'({code}). Rely on utilization signals instead.'
                ),
            }
        raise

    return {
        'region': region or os.environ.get('AWS_REGION', 'eu-west-1'),
        'total_dbs_with_recommendations': len(by_db),
        'recommendations_by_db': by_db,
    }


async def get_rds_recommendations(
    ctx: Context,
    client_id: str = Field(
        ...,
        description="Client identifier to use for this request."
    ),
    region: Optional[str] = Field(
        None,
        description="AWS region."
    ),
    db_instance_identifiers: Optional[List[str]] = Field(
        None,
        description=(
            "Filter to specific DB identifiers (defaults to all active/pending "
            "recommendations in the region)."
        )
    ),
) -> Dict[str, Any]:
    """Get AWS-native RDS recommendations (describe_db_recommendations).

    Returns the canonical AWS recommendations shown in the RDS console
    (e.g. 'Under-provisioned for system IOPS capacity'). Use this before
    downsizing any RDS candidate — if AWS flags the instance as under-provisioned,
    downsizing is wrong.

    Response is compacted to severity/category/recommendation/impact/reason
    and limited to 3 active recommendations per DB.
    """
    try:
        return await asyncio.to_thread(
            _fetch_rds_recommendations_sync,
            client_id,
            region,
            db_instance_identifiers,
        )
    except Exception as e:
        logger.error(f'Error getting RDS recommendations: {e}')
        return {'error': str(e)}
