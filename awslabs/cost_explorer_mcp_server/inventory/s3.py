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

"""S3 inventory handler."""

from typing import Any, Dict

from loguru import logger
from mcp.server.fastmcp import Context
from pydantic import Field

from ..aws_clients import get_s3_client
from .common import serialize_datetime


async def list_s3_buckets(
    ctx: Context,
    client_id: str = Field(
        ...,
        description="Client identifier to use for this request."
    ),
    include_lifecycle: bool = Field(
        True,
        description="Include lifecycle configuration for each bucket."
    ),
    include_versioning: bool = Field(
        True,
        description="Include versioning status for each bucket."
    ),
    include_encryption: bool = Field(
        True,
        description="Include encryption configuration for each bucket."
    ),
) -> Dict[str, Any]:
    """List S3 buckets with configuration details for cost optimization.

    Returns S3 bucket information including lifecycle rules, versioning,
    and encryption settings.
    """
    try:
        s3 = get_s3_client(client_id)
        
        response = s3.list_buckets()
        
        buckets = []
        for bucket in response.get('Buckets', []):
            bucket_name = bucket.get('Name')
            bucket_info = {
                'Name': bucket_name,
                'CreationDate': bucket.get('CreationDate'),
            }
            
            # Get bucket location
            try:
                location_response = s3.get_bucket_location(Bucket=bucket_name)
                bucket_info['Region'] = location_response.get('LocationConstraint') or 'us-east-1'
            except Exception as e:
                bucket_info['Region'] = 'unknown'
                bucket_info['LocationError'] = str(e)
            
            # Get lifecycle configuration
            if include_lifecycle:
                try:
                    lifecycle_response = s3.get_bucket_lifecycle_configuration(Bucket=bucket_name)
                    bucket_info['HasLifecycleRules'] = True
                    bucket_info['LifecycleRules'] = [
                        {
                            'ID': rule.get('ID'),
                            'Status': rule.get('Status'),
                            'Prefix': rule.get('Prefix', rule.get('Filter', {}).get('Prefix', '')),
                            'Transitions': rule.get('Transitions', []),
                            'Expiration': rule.get('Expiration'),
                            'NoncurrentVersionTransitions': rule.get('NoncurrentVersionTransitions', []),
                            'NoncurrentVersionExpiration': rule.get('NoncurrentVersionExpiration'),
                        }
                        for rule in lifecycle_response.get('Rules', [])
                    ]
                except s3.exceptions.ClientError as e:
                    if 'NoSuchLifecycleConfiguration' in str(e):
                        bucket_info['HasLifecycleRules'] = False
                        bucket_info['LifecycleRules'] = []
                    else:
                        bucket_info['LifecycleError'] = str(e)
                except Exception as e:
                    bucket_info['LifecycleError'] = str(e)
            
            # Get versioning status
            if include_versioning:
                try:
                    versioning_response = s3.get_bucket_versioning(Bucket=bucket_name)
                    bucket_info['VersioningStatus'] = versioning_response.get('Status', 'Disabled')
                    bucket_info['MFADelete'] = versioning_response.get('MFADelete', 'Disabled')
                except Exception as e:
                    bucket_info['VersioningError'] = str(e)
            
            # Get encryption configuration
            if include_encryption:
                try:
                    encryption_response = s3.get_bucket_encryption(Bucket=bucket_name)
                    rules = encryption_response.get('ServerSideEncryptionConfiguration', {}).get('Rules', [])
                    if rules:
                        bucket_info['EncryptionEnabled'] = True
                        bucket_info['EncryptionType'] = rules[0].get('ApplyServerSideEncryptionByDefault', {}).get('SSEAlgorithm')
                    else:
                        bucket_info['EncryptionEnabled'] = False
                except s3.exceptions.ClientError as e:
                    if 'ServerSideEncryptionConfigurationNotFoundError' in str(e):
                        bucket_info['EncryptionEnabled'] = False
                    else:
                        bucket_info['EncryptionError'] = str(e)
                except Exception as e:
                    bucket_info['EncryptionError'] = str(e)
            
            buckets.append(serialize_datetime(bucket_info))
        
        buckets_without_lifecycle = sum(
            1 for b in buckets 
            if b.get('HasLifecycleRules') is False
        )
        buckets_with_versioning = sum(
            1 for b in buckets 
            if b.get('VersioningStatus') == 'Enabled'
        )
        
        return {
            'buckets': buckets,
            'count': len(buckets),
            'buckets_without_lifecycle': buckets_without_lifecycle,
            'buckets_with_versioning': buckets_with_versioning,
        }
        
    except Exception as e:
        logger.error(f'Error listing S3 buckets: {e}')
        return {'error': str(e)}
