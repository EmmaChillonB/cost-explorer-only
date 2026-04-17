"""Tests for the savings commitments handler."""

import pytest
from unittest.mock import MagicMock, patch

from cost_optimizer.cost_explorer.savings import get_savings_commitments, _safe_float


class TestSafeFloat:
    """Test the _safe_float helper."""

    def test_none_returns_zero(self):
        assert _safe_float(None) == 0.0

    def test_valid_string(self):
        assert _safe_float('42.567') == 42.57

    def test_valid_int(self):
        assert _safe_float(10) == 10.0

    def test_valid_float(self):
        assert _safe_float(3.14159) == 3.14

    def test_invalid_string(self):
        assert _safe_float('not-a-number') == 0.0

    def test_empty_string(self):
        assert _safe_float('') == 0.0

    def test_zero(self):
        assert _safe_float(0) == 0.0

    def test_negative(self):
        assert _safe_float(-5.123) == -5.12


class TestGetSavingsCommitments:
    """Test the get_savings_commitments handler."""

    @pytest.mark.asyncio
    async def test_success_full_response(self):
        """Test full savings commitments response with all data."""
        mock_ce = MagicMock()

        # SP coverage
        mock_ce.get_savings_plans_coverage.return_value = {
            'SavingsPlansCoverages': [
                {
                    'Coverage': {
                        'SpendCoveredBySavingsPlans': '50.0',
                        'TotalCost': '100.0',
                    }
                }
            ]
        }

        # SP utilization
        mock_ce.get_savings_plans_utilization.return_value = {
            'Total': {
                'Utilization': {'UtilizationPercentage': '85.5'}
            }
        }

        # RI coverage
        mock_ce.get_reservation_coverage.return_value = {
            'CoveragesByTime': [
                {
                    'Total': {
                        'CoverageHours': {
                            'ReservedHours': '100.0',
                            'TotalRunningHours': '500.0',
                        }
                    }
                }
            ]
        }

        # RI utilization
        mock_ce.get_reservation_utilization.return_value = {
            'Total': {'UtilizationPercentage': '90.0'}
        }

        # On-demand spend
        mock_ce.get_cost_and_usage.return_value = {
            'ResultsByTime': [
                {
                    'Groups': [
                        {
                            'Keys': ['Amazon Elastic Compute Cloud - Compute'],
                            'Metrics': {'UnblendedCost': {'Amount': '500.0'}},
                        },
                        {
                            'Keys': ['Amazon Simple Storage Service'],
                            'Metrics': {'UnblendedCost': {'Amount': '100.0'}},
                        },
                    ]
                }
            ]
        }

        # SP recommendations
        mock_ce.get_savings_plans_purchase_recommendation.return_value = {
            'SavingsPlansPurchaseRecommendation': {
                'SavingsPlansPurchaseRecommendationSummary': {
                    'HourlyCommitmentToPurchase': '0.50',
                    'EstimatedMonthlySavingsAmount': '100.0',
                    'EstimatedSavingsPercentage': '20.0',
                },
                'SavingsPlansPurchaseRecommendationDetails': [
                    {
                        'SavingsPlansDetails': {'Region': 'us-east-1', 'InstanceFamily': 'c5'},
                        'HourlyCommitmentToPurchase': '0.50',
                        'EstimatedMonthlySavingsAmount': '100.0',
                        'EstimatedSavingsPercentage': '20.0',
                        'EstimatedSPCost': '80.0',
                        'EstimatedOnDemandCost': '20.0',
                        'EstimatedAverageUtilization': '85.0',
                    }
                ],
            }
        }

        # RI recommendations
        mock_ce.get_reservation_purchase_recommendation.return_value = {
            'Recommendations': [
                {
                    'RecommendationDetails': [
                        {
                            'InstanceDetails': {
                                'EC2InstanceDetails': {
                                    'InstanceType': 'c5.xlarge',
                                    'Region': 'us-east-1',
                                    'Platform': 'Linux/UNIX',
                                }
                            },
                            'RecommendedNumberOfInstancesToPurchase': '2',
                            'EstimatedMonthlySavingsAmount': '50.0',
                            'EstimatedMonthlyOnDemandCost': '200.0',
                            'RecurringStandardMonthlyCost': '150.0',
                            'MaximumNumberOfInstancesUsedPerHour': '4',
                            'AverageUtilization': '75.0',
                        }
                    ]
                }
            ]
        }

        mock_sp_client = MagicMock()
        mock_sp_client.describe_savings_plans.return_value = {
            'savingsPlans': [
                {
                    'savingsPlanType': 'Compute',
                    'paymentOption': 'No Upfront',
                    'state': 'active',
                    'start': '2025-01-01',
                    'end': '2026-01-01',
                    'commitment': '0.50',
                }
            ]
        }

        with patch(
            'cost_optimizer.cost_explorer.savings.get_cost_explorer_client',
            return_value=mock_ce,
        ), patch(
            'cost_optimizer.cost_explorer.savings.is_payer_account',
            return_value=False,
        ), patch(
            'cost_optimizer.cost_explorer.savings.build_account_filter',
            return_value=None,
        ), patch(
            'cost_optimizer.cost_explorer.savings.get_account_id',
            return_value='123456789012',
        ), patch(
            'cost_optimizer.aws_clients.get_aws_client',
            return_value=mock_sp_client,
        ):
            ctx = MagicMock()
            result = await get_savings_commitments(ctx, client_id='test-client', days_back=30, account_scope='auto')

        assert 'period' in result
        assert 'current_state' in result
        assert result['current_state']['sp_coverage_pct'] == 50.0
        assert result['current_state']['sp_utilization_pct'] == 85.5
        assert result['current_state']['ri_coverage_pct'] == 20.0
        assert result['current_state']['ri_utilization_pct'] == 90.0
        assert result['current_state']['has_active_savings_plans'] is True
        assert 'on_demand_eligible' in result
        # Only EC2 is in _ELIGIBLE_SERVICES, S3 is not
        assert len(result['on_demand_eligible']) == 1
        assert result['sp_recommendations'] is not None
        assert result['ri_recommendations'] is not None

    @pytest.mark.asyncio
    async def test_all_api_errors_handled(self):
        """Test that errors in individual API calls are handled gracefully."""
        mock_ce = MagicMock()
        mock_ce.get_savings_plans_coverage.side_effect = Exception('SP coverage error')
        mock_ce.get_savings_plans_utilization.side_effect = Exception('SP util error')
        mock_ce.get_reservation_coverage.side_effect = Exception('RI coverage error')
        mock_ce.get_reservation_utilization.side_effect = Exception('RI util error')
        mock_ce.get_cost_and_usage.side_effect = Exception('CE error')
        mock_ce.get_savings_plans_purchase_recommendation.side_effect = Exception('SP rec error')
        mock_ce.get_reservation_purchase_recommendation.side_effect = Exception('RI rec error')

        with patch(
            'cost_optimizer.cost_explorer.savings.get_cost_explorer_client',
            return_value=mock_ce,
        ), patch(
            'cost_optimizer.cost_explorer.savings.is_payer_account',
            return_value=False,
        ), patch(
            'cost_optimizer.cost_explorer.savings.build_account_filter',
            return_value=None,
        ), patch(
            'cost_optimizer.cost_explorer.savings.get_account_id',
            return_value='123456789012',
        ):
            ctx = MagicMock()
            result = await get_savings_commitments(ctx, client_id='test-client', days_back=30, account_scope='auto')

        # Should not error out entirely, just have defaults
        assert 'current_state' in result
        assert result['current_state']['sp_coverage_pct'] == 0
        assert result['sp_recommendations'] is None
        assert result['ri_recommendations'] is None

    @pytest.mark.asyncio
    async def test_top_level_exception(self):
        """Test top-level exception handling."""
        with patch(
            'cost_optimizer.cost_explorer.savings.get_cost_explorer_client',
            side_effect=Exception('Connection failed'),
        ):
            ctx = MagicMock()
            result = await get_savings_commitments(ctx, client_id='test-client', days_back=30, account_scope='auto')

        assert 'error' in result

    @pytest.mark.asyncio
    async def test_payer_account_with_scope(self):
        """Test with payer account that needs LINKED scope."""
        mock_ce = MagicMock()
        mock_ce.get_savings_plans_coverage.return_value = {'SavingsPlansCoverages': []}
        mock_ce.get_savings_plans_utilization.return_value = {'Total': {}}
        mock_ce.get_reservation_coverage.return_value = {'CoveragesByTime': []}
        mock_ce.get_reservation_utilization.return_value = {'Total': {}}
        mock_ce.get_cost_and_usage.return_value = {'ResultsByTime': []}
        mock_ce.get_savings_plans_purchase_recommendation.return_value = {
            'SavingsPlansPurchaseRecommendation': {}
        }
        mock_ce.get_reservation_purchase_recommendation.return_value = {'Recommendations': []}

        with patch(
            'cost_optimizer.cost_explorer.savings.get_cost_explorer_client',
            return_value=mock_ce,
        ), patch(
            'cost_optimizer.cost_explorer.savings.is_payer_account',
            return_value=True,
        ), patch(
            'cost_optimizer.cost_explorer.savings.build_account_filter',
            return_value=None,
        ), patch(
            'cost_optimizer.cost_explorer.savings.get_account_id',
            return_value='123456789012',
        ):
            ctx = MagicMock()
            result = await get_savings_commitments(ctx, client_id='test-client', days_back=30, account_scope='auto')

        assert 'current_state' in result
        # Verify SP recommendation called with AccountScope
        calls = mock_ce.get_savings_plans_purchase_recommendation.call_args_list
        assert any('AccountScope' in str(c) for c in calls)
