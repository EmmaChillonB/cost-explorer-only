"""Tests for EC2 inventory handler — covers _scan_region and list_ec2_regions_with_instances."""

import pytest
from unittest.mock import MagicMock, patch

from awslabs.cost_explorer_mcp_server.inventory.ec2 import (
    _scan_region,
    list_ec2_regions_with_instances,
    _REGIONS_CACHE,
)


class TestScanRegion:
    """Test the _scan_region sync helper."""

    def test_region_with_instances(self):
        mock_ec2 = MagicMock()
        paginator = MagicMock()
        mock_ec2.get_paginator.return_value = paginator
        paginator.paginate.return_value = [
            {
                'Reservations': [
                    {
                        'Instances': [
                            {'State': {'Name': 'running'}, 'InstanceType': 't3.micro'},
                            {'State': {'Name': 'stopped'}, 'InstanceType': 't3.micro'},
                            {'State': {'Name': 'terminated'}, 'InstanceType': 'c5.large'},
                        ]
                    }
                ]
            }
        ]

        with patch(
            'awslabs.cost_explorer_mcp_server.inventory.ec2.get_ec2_client',
            return_value=mock_ec2,
        ):
            result = _scan_region('test-client', 'us-east-1')

        assert result is not None
        assert result['region'] == 'us-east-1'
        assert result['total'] == 3
        assert result['running'] == 1
        assert result['stopped'] == 1
        assert len(result['instance_types']) == 2

    def test_region_no_instances(self):
        mock_ec2 = MagicMock()
        paginator = MagicMock()
        mock_ec2.get_paginator.return_value = paginator
        paginator.paginate.return_value = [{'Reservations': []}]

        with patch(
            'awslabs.cost_explorer_mcp_server.inventory.ec2.get_ec2_client',
            return_value=mock_ec2,
        ):
            result = _scan_region('test-client', 'eu-west-1')

        assert result is None

    def test_region_error(self):
        with patch(
            'awslabs.cost_explorer_mcp_server.inventory.ec2.get_ec2_client',
            side_effect=Exception('Region not enabled'),
        ):
            result = _scan_region('test-client', 'ap-south-2')

        assert result is None


class TestListEC2RegionsWithInstances:
    """Test the list_ec2_regions_with_instances handler."""

    @pytest.fixture(autouse=True)
    def clear_cache(self):
        """Clear the regions cache before each test."""
        _REGIONS_CACHE.clear()
        yield
        _REGIONS_CACHE.clear()

    @pytest.mark.asyncio
    async def test_success(self):
        mock_ec2 = MagicMock()
        mock_ec2.describe_regions.return_value = {
            'Regions': [
                {'RegionName': 'us-east-1'},
                {'RegionName': 'eu-west-1'},
            ]
        }

        # Mock the _scan_region to return data for us-east-1 only
        scan_results = {
            'us-east-1': {
                'region': 'us-east-1',
                'total': 3,
                'running': 2,
                'stopped': 1,
                'instance_types': [{'type': 't3.micro', 'count': 3}],
            },
            'eu-west-1': None,
        }

        with patch(
            'awslabs.cost_explorer_mcp_server.inventory.ec2.get_ec2_client',
            return_value=mock_ec2,
        ), patch(
            'awslabs.cost_explorer_mcp_server.inventory.ec2._scan_region',
            side_effect=lambda cid, r: scan_results.get(r),
        ):
            ctx = MagicMock()
            result = await list_ec2_regions_with_instances(ctx, client_id='test-client')

        assert 'summary' in result
        assert result['summary']['regions_with_instances'] == 1
        assert result['summary']['total_instances'] == 3
        assert result['summary']['total_running'] == 2
        assert result['summary']['total_stopped'] == 1
        assert len(result['regions']) == 1

    @pytest.mark.asyncio
    async def test_cached_result(self):
        # Pre-populate cache
        _REGIONS_CACHE['cached-client'] = {
            'regions': [],
            'summary': {'regions_with_instances': 0, 'total_instances': 0, 'total_running': 0, 'total_stopped': 0},
        }

        ctx = MagicMock()
        result = await list_ec2_regions_with_instances(ctx, client_id='cached-client')

        assert result['summary']['regions_with_instances'] == 0

    @pytest.mark.asyncio
    async def test_error(self):
        with patch(
            'awslabs.cost_explorer_mcp_server.inventory.ec2.get_ec2_client',
            side_effect=Exception('Connection failed'),
        ):
            ctx = MagicMock()
            result = await list_ec2_regions_with_instances(ctx, client_id='test-client')

        assert 'error' in result
