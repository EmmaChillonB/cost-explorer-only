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

"""AWS client factory for various AWS services.

This module provides a unified way to create AWS service clients (EC2, RDS, ELB, CloudWatch, S3, etc.)
by reusing the same authentication mechanism as Cost Explorer.
"""

import boto3
import os
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from loguru import logger
from typing import Any, Dict, Optional

from .auth import _load_clients_config, _get_role_arn_for_client

# Configure Loguru logging
logger.remove()
logger.add(sys.stderr, level=os.getenv('FASTMCP_LOG_LEVEL', 'WARNING'))

# Global client cache with thread safety - separate from Cost Explorer clients
_service_clients: Dict[str, Any] = {}  # {cache_key: client}
_service_sessions: Dict[str, boto3.Session] = {}  # {client_id: assumed_session}
_service_token_expiration: Dict[str, datetime] = {}  # {client_id: expiration}
_service_client_lock = threading.RLock()

# Buffer time before token expiration to trigger refresh
TOKEN_REFRESH_BUFFER_SECONDS = 300


def _is_service_token_expired(client_id: str) -> bool:
    """Check if token has expired or is about to expire."""
    if client_id not in _service_token_expiration:
        return True

    expiration_time = _service_token_expiration[client_id]
    now = datetime.now(timezone.utc)

    if now >= expiration_time - timedelta(seconds=TOKEN_REFRESH_BUFFER_SECONDS):
        logger.debug(f'Service token for {client_id[:8]} expired or expiring soon')
        return True

    return False


def _create_assumed_session(client_id: str) -> tuple[boto3.Session, datetime]:
    """Create a boto3 session with assumed role credentials.
    
    Uses the same approach as auth.py for consistency.
    
    Returns:
        Tuple of (session, token_expiration)
    """
    role_arn = _get_role_arn_for_client(client_id)
    if not role_arn:
        raise ValueError(f'Client {client_id} not found in clients.json')
    
    aws_region = os.environ.get('AWS_REGION', 'eu-west-1')
    
    # Create base session using the default credential chain
    # Same approach as auth.py - works with IAM Anywhere, instance roles, env vars, profiles
    logger.debug(f'Creating base session for {client_id[:8]}')
    session = boto3.Session(region_name=aws_region)
    
    safe_id = ''.join(c if c.isalnum() else '-' for c in client_id)[:12]
    session_name = f'svc-{safe_id}-{int(time.time())}'
    
    logger.debug(f'Assuming role {role_arn} with session {session_name}')
    sts_client = session.client('sts')
    assumed_role = sts_client.assume_role(
        RoleArn=role_arn,
        RoleSessionName=session_name
    )
    
    credentials = assumed_role['Credentials']
    
    assumed_session = boto3.Session(
        aws_access_key_id=credentials['AccessKeyId'],
        aws_secret_access_key=credentials['SecretAccessKey'],
        aws_session_token=credentials['SessionToken'],
        region_name=aws_region
    )
    
    if credentials['Expiration'].tzinfo is None:
        expiration_utc = credentials['Expiration'].replace(tzinfo=timezone.utc)
    else:
        expiration_utc = credentials['Expiration'].astimezone(timezone.utc)
    
    logger.info(f'Created assumed role session for {client_id[:8]}')
    return assumed_session, expiration_utc


def _get_or_create_session(client_id: str) -> boto3.Session:
    """Get existing session or create new one if expired."""
    with _service_client_lock:
        # Check if we have a valid cached session
        if client_id in _service_sessions and not _is_service_token_expired(client_id):
            logger.debug(f'Using cached session for {client_id[:8]}')
            return _service_sessions[client_id]
    
    # Create new session (outside lock to allow concurrent creation for different clients)
    session, expiration = _create_assumed_session(client_id)
    
    with _service_client_lock:
        _service_sessions[client_id] = session
        _service_token_expiration[client_id] = expiration
        # Clear cached clients for this client_id since credentials changed
        keys_to_remove = [k for k in _service_clients if k.startswith(f"{client_id}:")]
        for key in keys_to_remove:
            del _service_clients[key]
    
    return session


# Opt-in regions that may need regional STS AssumeRole
_OPT_IN_REGIONS = {
    'af-south-1', 'ap-east-1', 'ap-south-2', 'ap-southeast-3', 'ap-southeast-4',
    'eu-south-1', 'eu-south-2', 'eu-central-2', 'il-central-1', 'me-south-1',
    'me-central-1', 'ca-west-1',
}


def get_aws_client(client_id: str, service_name: str, region: Optional[str] = None) -> Any:
    """Get or create an AWS service client with automatic token refresh.

    For opt-in regions (eu-south-2, etc.), creates a separate session with
    STS AssumeRole called from within that region to avoid AuthFailure.

    Args:
        client_id: Client identifier from clients.json
        service_name: AWS service name (e.g., 'ec2', 'rds', 'cloudwatch', 's3', 'elb', 'elbv2')
        region: Optional AWS region override

    Returns:
        Boto3 service client
    """
    target_region = region or os.environ.get('AWS_REGION', 'eu-west-1')
    cache_key = f"{client_id}:{service_name}:{target_region}"

    with _service_client_lock:
        # Check if we have a valid cached client
        if cache_key in _service_clients and not _is_service_token_expired(client_id):
            logger.debug(f'Using cached {service_name} client for {client_id[:8]}')
            return _service_clients[cache_key]

    try:
        # For opt-in regions, use regional STS to assume role
        if target_region in _OPT_IN_REGIONS:
            client = _create_regional_client(client_id, service_name, target_region)
        else:
            # Standard path: reuse the main assumed session
            session = _get_or_create_session(client_id)
            client = session.client(service_name, region_name=target_region)

        with _service_client_lock:
            _service_clients[cache_key] = client

        logger.info(f'Created {service_name} client for {client_id[:8]} in {target_region}')
        return client

    except Exception as e:
        logger.error(f'Error creating {service_name} client for {client_id}: {e}')
        raise


def _create_regional_client(client_id: str, service_name: str, region: str) -> Any:
    """Create a service client for an opt-in region using regional STS.

    Opt-in regions (eu-south-2, etc.) require AssumeRole via their own
    regional STS endpoint. Tokens from the global/default STS may not work.
    """
    role_arn = _get_role_arn_for_client(client_id)
    if not role_arn:
        raise ValueError(f'Client {client_id} not found in clients.json')

    aws_region = os.environ.get('AWS_REGION', 'eu-west-1')
    base_session = boto3.Session(region_name=aws_region)

    safe_id = ''.join(c if c.isalnum() else '-' for c in client_id)[:12]
    session_name = f'reg-{safe_id}-{int(time.time())}'

    # Use the regional STS endpoint
    regional_sts = base_session.client(
        'sts',
        region_name=region,
        endpoint_url=f'https://sts.{region}.amazonaws.com',
    )
    assumed_role = regional_sts.assume_role(
        RoleArn=role_arn,
        RoleSessionName=session_name,
    )
    creds = assumed_role['Credentials']

    regional_session = boto3.Session(
        aws_access_key_id=creds['AccessKeyId'],
        aws_secret_access_key=creds['SecretAccessKey'],
        aws_session_token=creds['SessionToken'],
        region_name=region,
    )

    logger.info(f'Created regional {service_name} client for {client_id[:8]} in {region} (opt-in region)')
    return regional_session.client(service_name, region_name=region)


def get_ec2_client(client_id: str, region: Optional[str] = None) -> Any:
    """Get EC2 client for the specified client."""
    return get_aws_client(client_id, 'ec2', region)


def get_rds_client(client_id: str, region: Optional[str] = None) -> Any:
    """Get RDS client for the specified client."""
    return get_aws_client(client_id, 'rds', region)


def get_cloudwatch_client(client_id: str, region: Optional[str] = None) -> Any:
    """Get CloudWatch client for the specified client."""
    return get_aws_client(client_id, 'cloudwatch', region)


def get_s3_client(client_id: str, region: Optional[str] = None) -> Any:
    """Get S3 client for the specified client."""
    return get_aws_client(client_id, 's3', region)


def get_elbv2_client(client_id: str, region: Optional[str] = None) -> Any:
    """Get ELBv2 (Application/Network Load Balancer) client for the specified client."""
    return get_aws_client(client_id, 'elbv2', region)


def get_elb_client(client_id: str, region: Optional[str] = None) -> Any:
    """Get ELB (Classic Load Balancer) client for the specified client."""
    return get_aws_client(client_id, 'elb', region)


def clear_service_clients(client_id: str) -> None:
    """Clear all cached service clients for a client ID."""
    with _service_client_lock:
        keys_to_remove = [k for k in _service_clients if k.startswith(f"{client_id}:")]
        for key in keys_to_remove:
            del _service_clients[key]
        _service_sessions.pop(client_id, None)
        _service_token_expiration.pop(client_id, None)
    logger.info(f'Cleared service clients for {client_id[:8]}')
