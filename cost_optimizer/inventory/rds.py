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

import os
from typing import Any, Dict, Optional

from loguru import logger
from mcp.server.fastmcp import Context
from pydantic import Field

from ..aws_clients import get_rds_client
from .common import serialize_datetime


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
