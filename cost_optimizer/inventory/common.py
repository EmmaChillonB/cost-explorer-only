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

"""Common utilities for inventory handlers."""

import os
import sys
from datetime import datetime
from typing import Any, Dict, List

from loguru import logger

# Configure Loguru logging
logger.remove()
logger.add(sys.stderr, level=os.getenv('FASTMCP_LOG_LEVEL', 'WARNING'))

# Max parallel region scans. AWS has ~18 regions by default; scanning all in
# parallel is safe (each region uses its own regional endpoint).
REGIONS_MAX_CONCURRENT = 18

# Process-wide cache: client_id -> list of enabled region names.
# The set of enabled regions is effectively static during a workflow run.
_ENABLED_REGIONS_CACHE: Dict[str, List[str]] = {}


def serialize_datetime(obj: Any) -> Any:
    """Recursively convert datetime objects to ISO format strings."""
    if isinstance(obj, datetime):
        return obj.isoformat()
    elif isinstance(obj, dict):
        return {k: serialize_datetime(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [serialize_datetime(item) for item in obj]
    return obj


def list_enabled_regions(client_id: str) -> List[str]:
    """Return all AWS regions enabled for this account, cached per client_id.

    Sync — intended to be wrapped with ``asyncio.to_thread`` from async code.
    """
    if client_id in _ENABLED_REGIONS_CACHE:
        return _ENABLED_REGIONS_CACHE[client_id]

    # Lazy import to avoid circular dependency with aws_clients.
    from ..aws_clients import get_ec2_client

    ec2 = get_ec2_client(client_id, 'us-east-1')
    resp = ec2.describe_regions()
    regions = [r['RegionName'] for r in resp.get('Regions', [])]
    _ENABLED_REGIONS_CACHE[client_id] = regions
    return regions
