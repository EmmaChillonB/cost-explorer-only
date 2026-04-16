"""Extra tests for cost_explorer/validation.py to cover AWS validation paths and edge cases."""

import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from awslabs.cost_explorer_mcp_server.cost_explorer.validation import (
    validate_expression,
    validate_group_by,
    validate_forecast_date_range,
    validate_comparison_date_range,
)


class TestValidateExpressionWithAWSValidation:
    """Test validate_expression with VALIDATE_FILTER_VALUES=true."""

    def test_dimensions_with_aws_validation_valid(self):
        """Test that AWS validation is called when enabled."""
        expr = {
            'Dimensions': {
                'Key': 'SERVICE',
                'Values': ['Amazon EC2'],
                'MatchOptions': ['EQUALS'],
            }
        }
        mock_response = {'values': ['Amazon EC2', 'Amazon S3']}

        with patch(
            'awslabs.cost_explorer_mcp_server.cost_explorer.validation.VALIDATE_FILTER_VALUES',
            True,
        ), patch(
            'awslabs.cost_explorer_mcp_server.cost_explorer.helpers.get_available_dimension_values',
            return_value=mock_response,
        ):
            result = validate_expression(expr, '2025-01-01', '2025-01-31', 'test')

        assert result == {}

    def test_dimensions_with_aws_validation_invalid_value(self):
        """Test that invalid dimension value is caught with AWS validation."""
        expr = {
            'Dimensions': {
                'Key': 'SERVICE',
                'Values': ['NonExistentService'],
                'MatchOptions': ['EQUALS'],
            }
        }
        mock_response = {'values': ['Amazon EC2', 'Amazon S3']}

        with patch(
            'awslabs.cost_explorer_mcp_server.cost_explorer.validation.VALIDATE_FILTER_VALUES',
            True,
        ), patch(
            'awslabs.cost_explorer_mcp_server.cost_explorer.helpers.get_available_dimension_values',
            return_value=mock_response,
        ):
            result = validate_expression(expr, '2025-01-01', '2025-01-31', 'test')

        assert 'error' in result

    def test_dimensions_with_aws_validation_api_error(self):
        """Test handling of AWS API error during validation."""
        expr = {
            'Dimensions': {
                'Key': 'SERVICE',
                'Values': ['Amazon EC2'],
                'MatchOptions': ['EQUALS'],
            }
        }

        with patch(
            'awslabs.cost_explorer_mcp_server.cost_explorer.validation.VALIDATE_FILTER_VALUES',
            True,
        ), patch(
            'awslabs.cost_explorer_mcp_server.cost_explorer.helpers.get_available_dimension_values',
            return_value={'error': 'API call failed'},
        ):
            result = validate_expression(expr, '2025-01-01', '2025-01-31', 'test')

        assert 'error' in result

    def test_tags_with_aws_validation_valid(self):
        """Test tag validation with AWS validation enabled."""
        expr = {
            'Tags': {
                'Key': 'Environment',
                'Values': ['prod'],
                'MatchOptions': ['EQUALS'],
            }
        }
        mock_response = {'values': ['prod', 'dev', 'staging']}

        with patch(
            'awslabs.cost_explorer_mcp_server.cost_explorer.validation.VALIDATE_FILTER_VALUES',
            True,
        ), patch(
            'awslabs.cost_explorer_mcp_server.cost_explorer.helpers.get_available_tag_values',
            return_value=mock_response,
        ):
            result = validate_expression(expr, '2025-01-01', '2025-01-31', 'test')

        assert result == {}

    def test_tags_with_aws_validation_invalid_value(self):
        """Test invalid tag value detected with AWS validation."""
        expr = {
            'Tags': {
                'Key': 'Environment',
                'Values': ['nonexistent'],
                'MatchOptions': ['EQUALS'],
            }
        }
        mock_response = {'values': ['prod', 'dev']}

        with patch(
            'awslabs.cost_explorer_mcp_server.cost_explorer.validation.VALIDATE_FILTER_VALUES',
            True,
        ), patch(
            'awslabs.cost_explorer_mcp_server.cost_explorer.helpers.get_available_tag_values',
            return_value=mock_response,
        ):
            result = validate_expression(expr, '2025-01-01', '2025-01-31', 'test')

        assert 'error' in result

    def test_tags_with_aws_validation_api_error(self):
        """Test handling of tag API error during validation."""
        expr = {
            'Tags': {
                'Key': 'Environment',
                'Values': ['prod'],
                'MatchOptions': ['EQUALS'],
            }
        }

        with patch(
            'awslabs.cost_explorer_mcp_server.cost_explorer.validation.VALIDATE_FILTER_VALUES',
            True,
        ), patch(
            'awslabs.cost_explorer_mcp_server.cost_explorer.helpers.get_available_tag_values',
            return_value={'error': 'API call failed'},
        ):
            result = validate_expression(expr, '2025-01-01', '2025-01-31', 'test')

        assert 'error' in result


class TestValidateExpressionNestedLogical:
    """Test nested logical operator validation."""

    def test_and_with_nested_error(self):
        """Test And with a sub-expression that has an error."""
        expr = {
            'And': [
                {'Dimensions': {'Key': 'SERVICE', 'Values': ['EC2'], 'MatchOptions': ['EQUALS']}},
                {'Dimensions': {'Key': 'INVALID_KEY', 'Values': ['x'], 'MatchOptions': ['EQUALS']}},
            ]
        }
        result = validate_expression(expr, '2025-01-01', '2025-01-31', 'test')
        assert 'error' in result

    def test_or_with_nested_error(self):
        """Test Or with a sub-expression that has an error."""
        expr = {
            'Or': [
                {'Dimensions': {'Key': 'SERVICE', 'Values': ['EC2'], 'MatchOptions': ['EQUALS']}},
                {'Tags': {'Key': 'env'}},  # Missing Values and MatchOptions
            ]
        }
        result = validate_expression(expr, '2025-01-01', '2025-01-31', 'test')
        assert 'error' in result

    def test_not_with_nested_error(self):
        """Test Not with an invalid sub-expression."""
        expr = {
            'Not': {'Dimensions': {'Key': 'INVALID'}}
        }
        result = validate_expression(expr, '2025-01-01', '2025-01-31', 'test')
        assert 'error' in result

    def test_dimensions_invalid_match_options(self):
        """Test invalid match option for dimensions."""
        expr = {
            'Dimensions': {
                'Key': 'SERVICE',
                'Values': ['EC2'],
                'MatchOptions': ['STARTS_WITH'],
            }
        }
        result = validate_expression(expr, '2025-01-01', '2025-01-31', 'test')
        assert 'error' in result

    def test_tags_invalid_match_options(self):
        """Test invalid match option for tags."""
        expr = {
            'Tags': {
                'Key': 'Env',
                'Values': ['prod'],
                'MatchOptions': ['STARTS_WITH'],
            }
        }
        result = validate_expression(expr, '2025-01-01', '2025-01-31', 'test')
        assert 'error' in result

    def test_cost_categories_invalid_match_options(self):
        """Test invalid match option for cost categories."""
        expr = {
            'CostCategories': {
                'Key': 'Team',
                'Values': ['Eng'],
                'MatchOptions': ['STARTS_WITH'],
            }
        }
        result = validate_expression(expr, '2025-01-01', '2025-01-31', 'test')
        assert 'error' in result


class TestValidateComparisonEdgeCases:
    """Test comparison validation edge cases."""

    def test_comparison_start_in_current_month(self):
        """Test that start date in current month is rejected."""
        today = datetime.now(timezone.utc).date()
        start = today.replace(day=1)
        if start.month == 12:
            end = start.replace(year=start.year + 1, month=1)
        else:
            end = start.replace(month=start.month + 1)
        is_valid, error = validate_comparison_date_range(
            start.strftime('%Y-%m-%d'), end.strftime('%Y-%m-%d'),
        )
        assert not is_valid
        assert 'complete months' in error.lower() or 'not complete' in error.lower()

    def test_comparison_december_to_january(self):
        """Test December to January transition."""
        # Use a recent December within the 13-month lookback window
        today = datetime.now(timezone.utc).date()
        # Find the most recent December that's at least 2 months ago
        year = today.year
        if today.month <= 2:
            year -= 1
        if today.month <= 1:
            # In January, December of previous year may be too recent
            year -= 1
        start = datetime(year, 12, 1).date()
        end = datetime(year + 1, 1, 1).date()
        # Only test if within 13-month lookback
        months_back = (today.year - start.year) * 12 + (today.month - start.month)
        if months_back <= 13 and start < today.replace(day=1):
            is_valid, _ = validate_comparison_date_range(
                start.strftime('%Y-%m-%d'), end.strftime('%Y-%m-%d'),
            )
            assert is_valid

    def test_comparison_too_old(self):
        """Test date too far in the past (>13 months)."""
        today = datetime.now(timezone.utc).date()
        # Go 15 months back
        old_start = today.replace(day=1)
        for _ in range(15):
            if old_start.month == 1:
                old_start = old_start.replace(year=old_start.year - 1, month=12)
            else:
                old_start = old_start.replace(month=old_start.month - 1)
        if old_start.month == 12:
            old_end = old_start.replace(year=old_start.year + 1, month=1)
        else:
            old_end = old_start.replace(month=old_start.month + 1)
        is_valid, error = validate_comparison_date_range(
            old_start.strftime('%Y-%m-%d'), old_end.strftime('%Y-%m-%d'),
        )
        assert not is_valid
        assert '13 months' in error


class TestValidateForecastEdgeCases:
    """Test forecast validation edge cases."""

    def test_forecast_monthly_too_far(self):
        """Test monthly forecast that exceeds 12 months."""
        today = datetime.now(timezone.utc).date()
        start = today.strftime('%Y-%m-%d')
        far_future = today.replace(year=today.year + 2)
        end = far_future.strftime('%Y-%m-%d')
        is_valid, error = validate_forecast_date_range(start, end, 'MONTHLY')
        assert not is_valid

    def test_forecast_invalid_dates(self):
        """Test with invalid date format."""
        is_valid, error = validate_forecast_date_range('not-a-date', '2025-12-01', 'MONTHLY')
        assert not is_valid


class TestValidateGroupByEdgeCases:
    """Test group_by validation edge cases."""

    def test_group_by_not_dict(self):
        result = validate_group_by('SERVICE')
        assert 'error' in result

    def test_group_by_cost_category(self):
        result = validate_group_by({'Type': 'COST_CATEGORY', 'Key': 'Team'})
        assert result == {}

    def test_group_by_lowercase_type(self):
        result = validate_group_by({'Type': 'dimension', 'Key': 'SERVICE'})
        assert result == {}

    def test_group_by_exception(self):
        """Test exception handling in validate_group_by."""
        # Cause an exception by passing something that fails on .upper()
        result = validate_group_by({'Type': None, 'Key': 'SERVICE'})
        assert 'error' in result
