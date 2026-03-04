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

"""Common utilities for utilization handlers."""

import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

from loguru import logger

# Configure Loguru logging
logger.remove()
logger.add(sys.stderr, level=os.getenv('FASTMCP_LOG_LEVEL', 'WARNING'))

# Common CloudWatch statistics
VALID_STATISTICS = ['Average', 'Maximum', 'Minimum', 'Sum', 'SampleCount']

# Default periods for different granularities
DEFAULT_PERIODS = {
    'hour': 3600,
    'day': 86400,
    'minute': 60,
    '5minutes': 300,
}


def calculate_time_range(days_back: int = 7) -> tuple[datetime, datetime]:
    """Calculate start and end time for CloudWatch queries."""
    end_time = datetime.now(timezone.utc)
    start_time = end_time - timedelta(days=days_back)
    return start_time, end_time


def get_metric_statistics(
    cloudwatch_client,
    namespace: str,
    metric_name: str,
    dimensions: List[Dict[str, str]],
    start_time: datetime,
    end_time: datetime,
    period: int = 3600,
    statistics: List[str] = None,
) -> Dict[str, Any]:
    """Get metric statistics from CloudWatch."""
    if statistics is None:
        statistics = ['Average', 'Maximum', 'Minimum']
    
    try:
        logger.debug(f'Getting metric {namespace}/{metric_name} with dimensions {dimensions}')
        
        response = cloudwatch_client.get_metric_statistics(
            Namespace=namespace,
            MetricName=metric_name,
            Dimensions=dimensions,
            StartTime=start_time,
            EndTime=end_time,
            Period=period,
            Statistics=statistics,
        )
        
        datapoints = response.get('Datapoints', [])
        logger.debug(f'Got {len(datapoints)} datapoints for {metric_name}')
        
        # Sort by timestamp
        datapoints.sort(key=lambda x: x['Timestamp'])
        
        # Convert timestamps to ISO format
        for dp in datapoints:
            dp['Timestamp'] = dp['Timestamp'].isoformat()
        
        # Calculate summary statistics
        if datapoints:
            avg_values = [dp.get('Average', 0) for dp in datapoints if 'Average' in dp]
            max_values = [dp.get('Maximum', 0) for dp in datapoints if 'Maximum' in dp]
            min_values = [dp.get('Minimum', 0) for dp in datapoints if 'Minimum' in dp]
            
            summary = {
                'overall_average': round(sum(avg_values) / len(avg_values), 2) if avg_values else None,
                'overall_maximum': round(max(max_values), 2) if max_values else None,
                'overall_minimum': round(min(min_values), 2) if min_values else None,
                'datapoint_count': len(datapoints),
            }
        else:
            summary = {
                'overall_average': None,
                'overall_maximum': None,
                'overall_minimum': None,
                'datapoint_count': 0,
            }
        
        return {
            'metric_name': metric_name,
            'namespace': namespace,
            'datapoints': datapoints,
            'summary': summary,
        }
        
    except Exception as e:
        logger.error(f'Error getting metric {metric_name}: {e}')
        return {
            'metric_name': metric_name,
            'namespace': namespace,
            'error': str(e),
        }
