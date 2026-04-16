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

"""Metadata handlers (dimension/tag values) for the Cost Explorer package."""

import os
import sys
from datetime import datetime, timedelta, timezone
from loguru import logger
from mcp.server.fastmcp import Context
from pydantic import Field
from typing import Any, Dict, Optional

from .constants import VALID_DIMENSIONS
from ..auth import get_cost_explorer_client, build_account_filter
from .models import DateRange, DimensionKey


# Configure Loguru logging
logger.remove()
logger.add(sys.stderr, level=os.getenv('FASTMCP_LOG_LEVEL', 'WARNING'))


async def get_dimension_values(
    ctx: Context,
    dimension_key: DimensionKey,
    client_id: str = Field(
        ...,
        description="Client identifier to use for this request. Must exist in clients.json configuration."
    ),
    date_range: Optional[DateRange] = Field(
        None,
        description='The billing period start and end dates in YYYY-MM-DD format.'
    ),
    account_scope: str = Field(
        "auto",
        description="auto: filters payer accounts to own costs only (default). all: consolidated view of all linked accounts. linked: force single-account filter."
    ),
) -> Dict[str, Any]:
    """Get available values for a specific dimension.

    Retrieves valid values for filtering AWS Cost Explorer by a specific dimension
    (e.g., SERVICE, REGION). Use this to discover available filter values before
    querying costs.
    
    Args:
        ctx: MCP context
        dimension_key: The dimension to retrieve values for
        client_id: Client identifier for session management
        date_range: Optional date range (defaults to last 30 days if not provided)
    
    Returns:
        Dictionary with dimension name and list of available values
    """
    try:
        # Use default date range if not provided (last 30 days)
        if date_range is None:
            end_date = datetime.now(timezone.utc).strftime('%Y-%m-%d')
            start_date = (datetime.now(timezone.utc) - timedelta(days=30)).strftime('%Y-%m-%d')
        else:
            start_date = date_range.start_date
            end_date = date_range.end_date
        
        ce = get_cost_explorer_client(client_id)
        
        all_dimension_values = []
        next_token = None
        
        while True:
            params = {
                'TimePeriod': {'Start': start_date, 'End': end_date},
                'Dimension': dimension_key.dimension_key.upper(),
            }
            acct_filter = build_account_filter(client_id, account_scope)
            if acct_filter:
                params['Filter'] = acct_filter

            if next_token:
                params['NextPageToken'] = next_token

            response = ce.get_dimension_values(**params)
            
            dimension_values = response.get('DimensionValues', [])
            all_dimension_values.extend([value['Value'] for value in dimension_values])
            
            next_token = response.get('NextPageToken')
            if not next_token:
                break
        
        return {
            'dimension': dimension_key.dimension_key.upper(),
            'values': all_dimension_values,
            'count': len(all_dimension_values),
            'period': f'{start_date} to {end_date}',
        }
    except Exception as e:
        logger.error(f'Error getting dimension values: {e}')
        return {'error': str(e)}


async def get_tag_values(
    ctx: Context,
    tag_key: str = Field(
        ..., 
        description='The tag key to retrieve values for (e.g., "Environment", "Project").'
    ),
    client_id: str = Field(
        ...,
        description="Client identifier to use for this request. Must exist in clients.json configuration."
    ),
    date_range: Optional[DateRange] = Field(
        None,
        description='The billing period start and end dates in YYYY-MM-DD format.'
    ),
    account_scope: str = Field(
        "auto",
        description="auto: filters payer accounts to own costs only (default). all: consolidated view of all linked accounts. linked: force single-account filter."
    ),
) -> Dict[str, Any]:
    """Get available values for a specific tag key.

    Retrieves valid values for filtering AWS Cost Explorer by a specific tag key.
    Use this to discover available tag values before querying costs.
    
    Args:
        ctx: MCP context
        tag_key: The tag key to retrieve values for
        client_id: Client identifier for session management
        date_range: Optional date range (defaults to last 30 days if not provided)
    
    Returns:
        Dictionary with tag key and list of available values
    """
    try:
        # Use default date range if not provided (last 30 days)
        if date_range is None:
            end_date = datetime.now(timezone.utc).strftime('%Y-%m-%d')
            start_date = (datetime.now(timezone.utc) - timedelta(days=30)).strftime('%Y-%m-%d')
        else:
            start_date = date_range.start_date
            end_date = date_range.end_date
        
        ce = get_cost_explorer_client(client_id)
        
        all_tag_values = []
        next_token = None
        
        while True:
            params = {
                'TimePeriod': {'Start': start_date, 'End': end_date},
                'TagKey': tag_key,
            }
            acct_filter = build_account_filter(client_id, account_scope)
            if acct_filter:
                params['Filter'] = acct_filter

            if next_token:
                params['NextPageToken'] = next_token

            response = ce.get_tags(**params)
            
            tag_values = response.get('Tags', [])
            all_tag_values.extend(tag_values)
            
            next_token = response.get('NextPageToken')
            if not next_token:
                break
        
        return {
            'tag_key': tag_key,
            'values': all_tag_values,
            'count': len(all_tag_values),
            'period': f'{start_date} to {end_date}',
        }
    except Exception as e:
        logger.error(f'Error getting tag values: {e}')
        return {'error': str(e)}
