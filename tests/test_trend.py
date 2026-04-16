"""Tests for cost trend analysis with anomaly detection."""

import pytest
from unittest.mock import MagicMock, patch

from awslabs.cost_explorer_mcp_server.cost_explorer.trend import get_cost_trend_with_anomalies


class TestGetCostTrendWithAnomalies:
    """Test the cost trend analysis handler."""

    @pytest.mark.asyncio
    async def test_success_with_anomaly_and_drill_down(self):
        """Test full trend analysis with anomaly detection and drill-down."""
        mock_ce = MagicMock()

        # Monthly costs by service - EC2 spikes from 100 to 200
        mock_ce.get_cost_and_usage.side_effect = [
            # Main call: monthly costs
            {
                'ResultsByTime': [
                    {
                        'TimePeriod': {'Start': '2025-01-01'},
                        'Groups': [
                            {'Keys': ['Amazon EC2'], 'Metrics': {'UnblendedCost': {'Amount': '100.0'}}},
                            {'Keys': ['Amazon S3'], 'Metrics': {'UnblendedCost': {'Amount': '50.0'}}},
                        ],
                    },
                    {
                        'TimePeriod': {'Start': '2025-02-01'},
                        'Groups': [
                            {'Keys': ['Amazon EC2'], 'Metrics': {'UnblendedCost': {'Amount': '200.0'}}},
                            {'Keys': ['Amazon S3'], 'Metrics': {'UnblendedCost': {'Amount': '52.0'}}},
                        ],
                    },
                ]
            },
            # Drill-down: current month by usage type
            {
                'ResultsByTime': [
                    {
                        'Groups': [
                            {'Keys': ['BoxUsage:c5.large'], 'Metrics': {'UnblendedCost': {'Amount': '150.0'}}},
                            {'Keys': ['DataTransfer-Out'], 'Metrics': {'UnblendedCost': {'Amount': '50.0'}}},
                        ]
                    }
                ]
            },
            # Drill-down: previous month by usage type
            {
                'ResultsByTime': [
                    {
                        'Groups': [
                            {'Keys': ['BoxUsage:c5.large'], 'Metrics': {'UnblendedCost': {'Amount': '80.0'}}},
                            {'Keys': ['DataTransfer-Out'], 'Metrics': {'UnblendedCost': {'Amount': '20.0'}}},
                        ]
                    }
                ]
            },
        ]

        with patch(
            'awslabs.cost_explorer_mcp_server.cost_explorer.trend.get_cost_explorer_client',
            return_value=mock_ce,
        ), patch(
            'awslabs.cost_explorer_mcp_server.cost_explorer.trend.build_account_filter',
            return_value=None,
        ):
            ctx = MagicMock()
            result = await get_cost_trend_with_anomalies(
                ctx, client_id='test-client', months_back=2, anomaly_threshold_pct=10.0, account_scope='auto',
            )

        assert 'today_date' in result
        assert 'cost_monthly_trend' in result
        assert 'top_services_last_month' in result
        assert 'anomalies' in result
        assert 'api_calls_made' in result
        # EC2 had 100% growth, should be anomaly
        assert len(result['anomalies']) >= 1
        ec2_anomaly = next((a for a in result['anomalies'] if a['service'] == 'Amazon EC2'), None)
        assert ec2_anomaly is not None
        assert ec2_anomaly['abs_diff'] == 100.0
        # Should have drill-down data
        assert len(ec2_anomaly.get('top_drivers', [])) > 0

    @pytest.mark.asyncio
    async def test_no_anomalies(self):
        """Test when there are no anomalies."""
        mock_ce = MagicMock()
        mock_ce.get_cost_and_usage.return_value = {
            'ResultsByTime': [
                {
                    'TimePeriod': {'Start': '2025-01-01'},
                    'Groups': [
                        {'Keys': ['Amazon S3'], 'Metrics': {'UnblendedCost': {'Amount': '50.0'}}},
                    ],
                },
                {
                    'TimePeriod': {'Start': '2025-02-01'},
                    'Groups': [
                        {'Keys': ['Amazon S3'], 'Metrics': {'UnblendedCost': {'Amount': '51.0'}}},
                    ],
                },
            ]
        }

        with patch(
            'awslabs.cost_explorer_mcp_server.cost_explorer.trend.get_cost_explorer_client',
            return_value=mock_ce,
        ), patch(
            'awslabs.cost_explorer_mcp_server.cost_explorer.trend.build_account_filter',
            return_value=None,
        ):
            ctx = MagicMock()
            result = await get_cost_trend_with_anomalies(
                ctx, client_id='test-client', months_back=2, anomaly_threshold_pct=10.0, account_scope='auto',
            )

        assert result['anomalies'] == []

    @pytest.mark.asyncio
    async def test_new_service_anomaly(self):
        """Test detection of new service appearing as anomaly."""
        mock_ce = MagicMock()
        mock_ce.get_cost_and_usage.return_value = {
            'ResultsByTime': [
                {
                    'TimePeriod': {'Start': '2025-01-01'},
                    'Groups': [
                        {'Keys': ['Amazon S3'], 'Metrics': {'UnblendedCost': {'Amount': '50.0'}}},
                    ],
                },
                {
                    'TimePeriod': {'Start': '2025-02-01'},
                    'Groups': [
                        {'Keys': ['Amazon S3'], 'Metrics': {'UnblendedCost': {'Amount': '50.0'}}},
                        {'Keys': ['Amazon SageMaker'], 'Metrics': {'UnblendedCost': {'Amount': '100.0'}}},
                    ],
                },
            ]
        }

        with patch(
            'awslabs.cost_explorer_mcp_server.cost_explorer.trend.get_cost_explorer_client',
            return_value=mock_ce,
        ), patch(
            'awslabs.cost_explorer_mcp_server.cost_explorer.trend.build_account_filter',
            return_value=None,
        ):
            ctx = MagicMock()
            result = await get_cost_trend_with_anomalies(
                ctx, client_id='test-client', months_back=2, anomaly_threshold_pct=10.0, account_scope='auto',
            )

        # New service > $20 should appear as anomaly
        assert len(result['anomalies']) >= 1

    @pytest.mark.asyncio
    async def test_excluded_services_ignored(self):
        """Test that Tax and AWS Support are excluded from anomaly detection."""
        mock_ce = MagicMock()
        mock_ce.get_cost_and_usage.return_value = {
            'ResultsByTime': [
                {
                    'TimePeriod': {'Start': '2025-01-01'},
                    'Groups': [
                        {'Keys': ['Tax'], 'Metrics': {'UnblendedCost': {'Amount': '10.0'}}},
                        {'Keys': ['AWS Support'], 'Metrics': {'UnblendedCost': {'Amount': '100.0'}}},
                    ],
                },
                {
                    'TimePeriod': {'Start': '2025-02-01'},
                    'Groups': [
                        {'Keys': ['Tax'], 'Metrics': {'UnblendedCost': {'Amount': '1000.0'}}},
                        {'Keys': ['AWS Support'], 'Metrics': {'UnblendedCost': {'Amount': '1000.0'}}},
                    ],
                },
            ]
        }

        with patch(
            'awslabs.cost_explorer_mcp_server.cost_explorer.trend.get_cost_explorer_client',
            return_value=mock_ce,
        ), patch(
            'awslabs.cost_explorer_mcp_server.cost_explorer.trend.build_account_filter',
            return_value=None,
        ):
            ctx = MagicMock()
            result = await get_cost_trend_with_anomalies(
                ctx, client_id='test-client', months_back=2, anomaly_threshold_pct=10.0, account_scope='auto',
            )

        assert result['anomalies'] == []

    @pytest.mark.asyncio
    async def test_top_level_error(self):
        """Test top-level error handling."""
        with patch(
            'awslabs.cost_explorer_mcp_server.cost_explorer.trend.get_cost_explorer_client',
            side_effect=Exception('Connection failed'),
        ):
            ctx = MagicMock()
            result = await get_cost_trend_with_anomalies(ctx, client_id='test-client', months_back=6, anomaly_threshold_pct=10.0, account_scope='auto')

        assert 'error' in result

    @pytest.mark.asyncio
    async def test_drill_down_error_handled(self):
        """Test that drill-down errors don't crash the analysis."""
        mock_ce = MagicMock()
        call_count = 0

        def mock_get_cost(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return {
                    'ResultsByTime': [
                        {
                            'TimePeriod': {'Start': '2025-01-01'},
                            'Groups': [
                                {'Keys': ['Amazon EC2'], 'Metrics': {'UnblendedCost': {'Amount': '100.0'}}},
                            ],
                        },
                        {
                            'TimePeriod': {'Start': '2025-02-01'},
                            'Groups': [
                                {'Keys': ['Amazon EC2'], 'Metrics': {'UnblendedCost': {'Amount': '500.0'}}},
                            ],
                        },
                    ]
                }
            raise Exception('Drill-down failed')

        mock_ce.get_cost_and_usage.side_effect = mock_get_cost

        with patch(
            'awslabs.cost_explorer_mcp_server.cost_explorer.trend.get_cost_explorer_client',
            return_value=mock_ce,
        ), patch(
            'awslabs.cost_explorer_mcp_server.cost_explorer.trend.build_account_filter',
            return_value=None,
        ):
            ctx = MagicMock()
            result = await get_cost_trend_with_anomalies(
                ctx, client_id='test-client', months_back=2, anomaly_threshold_pct=10.0, account_scope='auto',
            )

        assert 'anomalies' in result
        assert len(result['anomalies']) >= 1
        # Should have drill_down_error instead of top_drivers
        anomaly = result['anomalies'][0]
        assert 'drill_down_error' in anomaly

    @pytest.mark.asyncio
    async def test_with_account_filter(self):
        """Test that account filter is applied when present."""
        mock_ce = MagicMock()
        mock_ce.get_cost_and_usage.return_value = {
            'ResultsByTime': [
                {
                    'TimePeriod': {'Start': '2025-01-01'},
                    'Groups': [
                        {'Keys': ['Amazon S3'], 'Metrics': {'UnblendedCost': {'Amount': '50.0'}}},
                    ],
                },
            ]
        }

        mock_filter = {'Dimensions': {'Key': 'LINKED_ACCOUNT', 'Values': ['123']}}

        with patch(
            'awslabs.cost_explorer_mcp_server.cost_explorer.trend.get_cost_explorer_client',
            return_value=mock_ce,
        ), patch(
            'awslabs.cost_explorer_mcp_server.cost_explorer.trend.build_account_filter',
            return_value=mock_filter,
        ):
            ctx = MagicMock()
            result = await get_cost_trend_with_anomalies(
                ctx, client_id='test-client', months_back=1, anomaly_threshold_pct=10.0, account_scope='auto',
            )

        # Verify filter was passed to API call
        call_kwargs = mock_ce.get_cost_and_usage.call_args[1]
        assert 'Filter' in call_kwargs
