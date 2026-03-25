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

"""Cost Explorer package - AWS Cost Explorer API tools.

This package provides tools for analyzing AWS costs and usage data:
- Cost and usage queries
- Cost comparisons between periods
- Cost forecasting
- Dimension and tag value lookups
"""

from .usage import get_cost_and_usage
from .comparison import get_cost_and_usage_comparisons, get_cost_comparison_drivers
from .forecast import get_cost_forecast
from .metadata import get_dimension_values, get_tag_values
from .utility import get_today_date
from .trend import get_cost_trend_with_anomalies

__all__ = [
    # Usage
    'get_cost_and_usage',
    # Comparison
    'get_cost_and_usage_comparisons',
    'get_cost_comparison_drivers',
    # Forecast
    'get_cost_forecast',
    # Metadata
    'get_dimension_values',
    'get_tag_values',
    # Utility
    'get_today_date',
    # Trend analysis
    'get_cost_trend_with_anomalies',
]
