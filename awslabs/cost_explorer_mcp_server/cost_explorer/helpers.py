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

"""Helper functions for the Cost Explorer package."""

import os
import sys
from datetime import datetime
from typing import Any, Dict

from loguru import logger

# Import from auth module for functions that need AWS client
from ..auth import get_cost_explorer_client, build_account_filter

# Configure Loguru logging
logger.remove()
logger.add(sys.stderr, level=os.getenv("FASTMCP_LOG_LEVEL", "WARNING"))


def get_available_dimension_values(
    key: str, billing_period_start: str, billing_period_end: str, client_id: str,
    account_scope: str = "auto",
) -> Dict[str, Any]:
    """Get available values for a specific dimension with pagination support.

    Args:
        key: The dimension key to retrieve values for
        billing_period_start: Start date in YYYY-MM-DD format
        billing_period_end: End date in YYYY-MM-DD format
        client_id: Client identifier (required for assuming role)
        account_scope: LINKED or PAYER
        
    Returns:
        Dictionary with dimension values or error
    """
    try:
        ce = get_cost_explorer_client(client_id)
        
        all_dimension_values = []
        next_token = None

        while True:
            params = {
                'TimePeriod': {'Start': billing_period_start, 'End': billing_period_end},
                'Dimension': key.upper(),
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

        return {'dimension': key.upper(), 'values': all_dimension_values}
    except Exception as e:
        logger.error(
            f'Error getting dimension values for {key.upper()} ({billing_period_start} to {billing_period_end}): {e}'
        )
        return {'error': str(e)}


def get_available_tag_values(
    tag_key: str, billing_period_start: str, billing_period_end: str, client_id: str,
    account_scope: str = "auto",
) -> Dict[str, Any]:
    """Get available values for a specific tag key with pagination support.

    Args:
        tag_key: The tag key to retrieve values for
        billing_period_start: Start date in YYYY-MM-DD format
        billing_period_end: End date in YYYY-MM-DD format
        client_id: Client identifier (required for assuming role)
        account_scope: LINKED or PAYER
        
    Returns:
        Dictionary with tag values or error
    """
    try:
        ce = get_cost_explorer_client(client_id)
        
        all_tag_values = []
        next_token = None

        while True:
            params = {
                'TimePeriod': {'Start': billing_period_start, 'End': billing_period_end},
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

        return {'tag_key': tag_key, 'values': all_tag_values}
    except Exception as e:
        logger.error(
            f'Error getting tag values for {tag_key} ({billing_period_start} to {billing_period_end}): {e}'
        )
        return {'error': str(e)}


def format_date_for_api(date_str: str, granularity: str) -> str:
    """Format date string appropriately for AWS Cost Explorer API based on granularity.

    Args:
        date_str: Date string in YYYY-MM-DD format
        granularity: The granularity (DAILY, MONTHLY, HOURLY)

    Returns:
        Formatted date string appropriate for the API call
    """
    if granularity.upper() == 'HOURLY':
        # For hourly granularity, AWS expects datetime format
        # Convert YYYY-MM-DD to YYYY-MM-DDTHH:MM:SSZ
        dt = datetime.strptime(date_str, '%Y-%m-%d')
        return dt.strftime('%Y-%m-%dT00:00:00Z')
    else:
        # For DAILY and MONTHLY, use the original date format
        return date_str


def extract_group_key_from_complex_selector(
    selector: Dict[str, Any], group_by: Dict[str, str]
) -> str:
    """Extract group key from complex CostSelector structures dynamically.

    Args:
        selector: The CostSelector dictionary from API response
        group_by: The GroupBy dictionary with Type and Key

    Returns:
        String representing the group key
    """
    group_type = group_by.get('Type', '').upper()
    group_key = group_by.get('Key', '')

    def search_for_group_key(sel_part):
        """Recursively search for the group key in any part of the selector."""
        if isinstance(sel_part, dict):
            # Check if this is the structure we're looking for
            if group_type == 'DIMENSION' and 'Dimensions' in sel_part:
                dim_info = sel_part['Dimensions']
                if dim_info.get('Key') == group_key and 'Values' in dim_info:
                    values = dim_info['Values']
                    return values[0] if values and values[0] else f'No {group_key}'

            elif group_type == 'TAG' and 'Tags' in sel_part:
                tag_info = sel_part['Tags']
                if tag_info.get('Key') == group_key and 'Values' in tag_info:
                    values = tag_info['Values']
                    return values[0] if values and values[0] else f'No {group_key}'

            elif group_type == 'COST_CATEGORY' and 'CostCategories' in sel_part:
                cc_info = sel_part['CostCategories']
                if cc_info.get('Key') == group_key and 'Values' in cc_info:
                    values = cc_info['Values']
                    return values[0] if values and values[0] else f'No {group_key}'

            # Recursively search in nested structures
            for key, value in sel_part.items():
                if key in ['And', 'Or'] and isinstance(value, list):
                    for item in value:
                        result = search_for_group_key(item)
                        if result:
                            return result
                elif key == 'Not' and isinstance(value, dict):
                    result = search_for_group_key(value)
                    if result:
                        return result

        return None

    result = search_for_group_key(selector)
    return result if result else 'Unknown'


def extract_usage_context_from_selector(selector: Dict[str, Any]) -> Dict[str, str]:
    """Extract all available context from complex selectors dynamically.

    Args:
        selector: The CostSelector dictionary from API response

    Returns:
        Dictionary with all available context information
    """
    context = {}

    def extract_from_structure(sel_part):
        """Recursively extract context from any part of the selector."""
        if isinstance(sel_part, dict):
            # Extract from Dimensions
            if 'Dimensions' in sel_part:
                dim_info = sel_part['Dimensions']
                key = dim_info.get('Key', '')
                values = dim_info.get('Values', [])
                if values and values[0]:  # Skip empty values
                    context[key.lower()] = values[0]

            # Extract from Tags
            if 'Tags' in sel_part:
                tag_info = sel_part['Tags']
                tag_key = tag_info.get('Key', '')
                values = tag_info.get('Values', [])
                if values and values[0]:
                    context[f'tag_{tag_key.lower()}'] = values[0]

            # Extract from CostCategories
            if 'CostCategories' in sel_part:
                cc_info = sel_part['CostCategories']
                cc_key = cc_info.get('Key', '')
                values = cc_info.get('Values', [])
                if values and values[0]:
                    context[f'category_{cc_key.lower()}'] = values[0]

            # Recursively process nested structures
            for key, value in sel_part.items():
                if key in ['And', 'Or'] and isinstance(value, list):
                    for item in value:
                        extract_from_structure(item)
                elif key == 'Not' and isinstance(value, dict):
                    extract_from_structure(value)

    extract_from_structure(selector)
    return context


def create_detailed_group_key(
    group_key: str, context: Dict[str, str], group_by: Dict[str, str]
) -> str:
    """Create a detailed group key that includes relevant context.

    Args:
        group_key: The primary group key extracted from the selector
        context: Additional context from the selector
        group_by: The GroupBy dictionary with Type and Key

    Returns:
        Enhanced group key with context
    """
    service = context.get('service', '')
    usage_type = context.get('usage_type', '')

    # Create a meaningful key based on what's available
    parts = [group_key]

    # Add service context if it's not the group key itself
    if service and group_by.get('Key') != 'SERVICE':
        parts.append(service)

    # Add usage type in parentheses for specificity
    if usage_type:
        return f'{" - ".join(parts)} ({usage_type})'

    return ' - '.join(parts)
