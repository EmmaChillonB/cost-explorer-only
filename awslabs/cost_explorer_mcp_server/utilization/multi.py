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
    """Get utilization metrics for multiple resources at once.

    This is a convenience tool that retrieves utilization for all running
    EC2 instances and RDS databases in a region.
    """
    try:
        result = {
            'region': region or os.environ.get('AWS_REGION', 'eu-west-1'),
            'days_back': days_back,
            'ec2': {},
            'rds': {},
            'summary': {
                'underutilized_ec2': [],
                'underutilized_rds': [],
                'total_recommendations': 0,
            },
        }
        
        if include_ec2:
            ec2_client = get_ec2_client(client_id, region)
            
            filters = ec2_filters or [{'Name': 'instance-state-name', 'Values': ['running']}]
            
            instances = []
            paginator = ec2_client.get_paginator('describe_instances')
            for page in paginator.paginate(Filters=filters):
                for reservation in page.get('Reservations', []):
                    for instance in reservation.get('Instances', []):
                        instances.append(instance.get('InstanceId'))
            
            result['ec2']['instance_count'] = len(instances)
            result['ec2']['instances'] = []
            
            for instance_id in instances[:10]:
                util = await get_ec2_utilization(
                    ctx, client_id, instance_id, region, days_back
                )
                
                summary = {
                    'instance_id': instance_id,
                    'cpu_avg': util.get('metrics', {}).get('cpu', {}).get('summary', {}).get('overall_average'),
                    'status': util.get('assessment', {}).get('status'),
                }
                result['ec2']['instances'].append(summary)
                
                if summary.get('status') in ['underutilized', 'significantly_underutilized']:
                    result['summary']['underutilized_ec2'].append(instance_id)
            
            if len(instances) > 10:
                result['ec2']['note'] = f'Showing first 10 of {len(instances)} instances'
        
        if include_rds:
            rds_client = get_rds_client(client_id, region)
            
            db_instances = []
            paginator = rds_client.get_paginator('describe_db_instances')
            for page in paginator.paginate():
                for db in page.get('DBInstances', []):
                    if db.get('DBInstanceStatus') == 'available':
                        db_instances.append(db.get('DBInstanceIdentifier'))
            
            result['rds']['instance_count'] = len(db_instances)
            result['rds']['instances'] = []
            
            for db_id in db_instances[:10]:
                util = await get_rds_utilization(
                    ctx, client_id, db_id, region, days_back
                )
                
                summary = {
                    'db_instance_identifier': db_id,
                    'cpu_avg': util.get('metrics', {}).get('cpu', {}).get('summary', {}).get('overall_average'),
                    'status': util.get('assessment', {}).get('status'),
                }
                result['rds']['instances'].append(summary)
                
                if summary.get('status') == 'underutilized':
                    result['summary']['underutilized_rds'].append(db_id)
            
            if len(db_instances) > 10:
                result['rds']['note'] = f'Showing first 10 of {len(db_instances)} instances'
        
        result['summary']['total_recommendations'] = (
            len(result['summary']['underutilized_ec2']) +
            len(result['summary']['underutilized_rds'])
        )
        
        return result
        
    except Exception as e:
        logger.error(f'Error getting multi-resource utilization: {e}')
        return {'error': str(e)}
