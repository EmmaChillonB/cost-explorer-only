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

"""Tests for metadata module (dimension/tag value retrieval)."""

import pytest
from cost_optimizer.cost_explorer.helpers import (
    get_available_dimension_values,
    get_available_tag_values,
)
from cost_optimizer.cost_explorer.metadata import get_dimension_values, get_tag_values
from cost_optimizer.cost_explorer.models import DateRange, DimensionKey
from unittest.mock import MagicMock, patch


@pytest.fixture
def mock_ce_client():
    """Mock Cost Explorer client."""
    with patch('cost_optimizer.cost_explorer.metadata.get_cost_explorer_client') as mock:
        client = MagicMock()
        mock.return_value = client
        yield client


@pytest.fixture
def mock_helpers_ce_client():
    """Mock Cost Explorer client for helpers module."""
    with patch('cost_optimizer.cost_explorer.helpers.get_cost_explorer_client') as mock:
        client = MagicMock()
        mock.return_value = client
        yield client


@pytest.fixture
def valid_date_range():
    """Valid date range for testing."""
    return DateRange(start_date='2025-01-01', end_date='2025-01-31')


@pytest.fixture
def valid_dimension():
    """Valid dimension for testing."""
    return DimensionKey(dimension_key='SERVICE')


class TestDimensionValues:
    """Test dimension value retrieval."""

    @pytest.mark.asyncio
    async def test_get_dimension_values_success(
        self, mock_ce_client, valid_date_range, valid_dimension
    ):
        """Test successful dimension values retrieval."""
        mock_ce_client.get_dimension_values.return_value = {
            'DimensionValues': [
                {'Value': 'Amazon Elastic Compute Cloud - Compute'},
                {'Value': 'Amazon Simple Storage Service'},
            ]
        }

        ctx = MagicMock()
        with patch('cost_optimizer.cost_explorer.metadata.build_account_filter', return_value=None):
            result = await get_dimension_values(
                ctx, dimension_key=valid_dimension, client_id='test-client',
                date_range=valid_date_range,
            )

        assert result['dimension'] == 'SERVICE'
        assert len(result['values']) == 2
        assert 'Amazon Elastic Compute Cloud - Compute' in result['values']

    @pytest.mark.asyncio
    async def test_get_dimension_values_error(
        self, mock_ce_client, valid_date_range, valid_dimension
    ):
        """Test dimension values retrieval with error."""
        mock_ce_client.get_dimension_values.side_effect = Exception('API Error')

        ctx = MagicMock()
        with patch('cost_optimizer.cost_explorer.metadata.build_account_filter', return_value=None):
            result = await get_dimension_values(
                ctx, dimension_key=valid_dimension, client_id='test-client',
                date_range=valid_date_range,
            )

        assert 'error' in result
        assert 'API Error' in result['error']


class TestTagValues:
    """Test tag value retrieval."""

    @pytest.mark.asyncio
    async def test_get_tag_values_success(self, mock_ce_client, valid_date_range):
        """Test successful tag values retrieval."""
        mock_ce_client.get_tags.return_value = {'Tags': ['dev', 'prod', 'test']}

        ctx = MagicMock()
        with patch('cost_optimizer.cost_explorer.metadata.build_account_filter', return_value=None):
            result = await get_tag_values(
                ctx, tag_key='Environment', client_id='test-client',
                date_range=valid_date_range,
            )

        assert result['tag_key'] == 'Environment'
        assert result['values'] == ['dev', 'prod', 'test']

    @pytest.mark.asyncio
    async def test_get_tag_values_error(self, mock_ce_client, valid_date_range):
        """Test tag values retrieval with error."""
        mock_ce_client.get_tags.side_effect = Exception('API Error')

        ctx = MagicMock()
        with patch('cost_optimizer.cost_explorer.metadata.build_account_filter', return_value=None):
            result = await get_tag_values(
                ctx, tag_key='Environment', client_id='test-client',
                date_range=valid_date_range,
            )

        assert 'error' in result


class TestImplementationFunctions:
    """Tests for the helper implementation functions."""

    @patch('cost_optimizer.cost_explorer.helpers.get_cost_explorer_client')
    def test_get_available_dimension_values_success(self, mock_get_client):
        """Test successful dimension values retrieval."""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.get_dimension_values.return_value = {
            'DimensionValues': [
                {'Value': 'EC2', 'Attributes': {}},
                {'Value': 'S3', 'Attributes': {}},
            ]
        }

        result = get_available_dimension_values('SERVICE', '2025-01-01', '2025-01-31', 'test-client')

        assert 'values' in result
        assert 'EC2' in result['values']
        assert 'S3' in result['values']
        mock_client.get_dimension_values.assert_called_once()

    @patch('cost_optimizer.cost_explorer.helpers.get_cost_explorer_client')
    def test_get_available_dimension_values_error(self, mock_get_client):
        """Test dimension values retrieval with error."""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.get_dimension_values.side_effect = Exception('API Error')

        result = get_available_dimension_values('SERVICE', '2025-01-01', '2025-01-31', 'test-client')

        assert 'error' in result
        assert 'API Error' in result['error']

    @patch('cost_optimizer.cost_explorer.helpers.get_cost_explorer_client')
    def test_get_available_tag_values_success(self, mock_get_client):
        """Test successful tag values retrieval."""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.get_tags.return_value = {'Tags': ['Production', 'Development', 'Testing']}

        result = get_available_tag_values('Environment', '2025-01-01', '2025-01-31', 'test-client')

        assert 'values' in result
        assert 'Production' in result['values']
        assert 'Development' in result['values']
        mock_client.get_tags.assert_called_once()

    @patch('cost_optimizer.cost_explorer.helpers.get_cost_explorer_client')
    def test_get_available_tag_values_error(self, mock_get_client):
        """Test tag values retrieval with error."""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.get_tags.side_effect = Exception('API Error')

        result = get_available_tag_values('Environment', '2025-01-01', '2025-01-31', 'test-client')

        assert 'error' in result
        assert 'API Error' in result['error']

    @pytest.mark.asyncio
    async def test_get_dimension_values_exception(self):
        """Test get_dimension_values with exception from client."""
        with patch('cost_optimizer.cost_explorer.metadata.get_cost_explorer_client') as mock_get_client:
            mock_get_client.side_effect = Exception('Unexpected error')
            with patch('cost_optimizer.cost_explorer.metadata.build_account_filter', return_value=None):
                ctx = MagicMock()
                date_range = DateRange(start_date='2025-01-01', end_date='2025-01-31')
                dimension = DimensionKey(dimension_key='SERVICE')

                result = await get_dimension_values(
                    ctx, dimension_key=dimension, client_id='test-client',
                    date_range=date_range,
                )

        assert 'error' in result

    @pytest.mark.asyncio
    async def test_get_tag_values_exception(self):
        """Test get_tag_values with exception from client."""
        with patch('cost_optimizer.cost_explorer.metadata.get_cost_explorer_client') as mock_get_client:
            mock_get_client.side_effect = Exception('Unexpected tag error')
            with patch('cost_optimizer.cost_explorer.metadata.build_account_filter', return_value=None):
                ctx = MagicMock()
                date_range = DateRange(start_date='2025-01-01', end_date='2025-01-31')

                result = await get_tag_values(
                    ctx, tag_key='Environment', client_id='test-client',
                    date_range=date_range,
                )

        assert 'error' in result
