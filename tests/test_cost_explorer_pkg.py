"""Tests for cost_explorer sub-package modules: metadata, helpers, validation, models."""

import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from cost_optimizer.cost_explorer.models import DateRange, DimensionKey
from cost_optimizer.cost_explorer.metadata import (
    get_dimension_values as ce_get_dimension_values,
    get_tag_values as ce_get_tag_values,
)
from cost_optimizer.cost_explorer.helpers import (
    get_available_dimension_values,
    get_available_tag_values,
    format_date_for_api,
    extract_group_key_from_complex_selector,
    extract_usage_context_from_selector,
    create_detailed_group_key,
)
from cost_optimizer.cost_explorer.validation import (
    validate_dimension_key,
    validate_date_format,
    validate_date_range,
    validate_match_options,
    validate_expression,
    validate_group_by,
    validate_forecast_date_range,
    validate_comparison_date_range,
)


# ──────────────────────────── cost_explorer/models.py ─────────────────────────


class TestCostExplorerDateRange:
    """Test the cost_explorer package DateRange model."""

    def test_valid_range(self):
        dr = DateRange(start_date='2025-01-01', end_date='2025-01-31')
        assert dr.start_date == '2025-01-01'

    def test_invalid_date_format(self):
        with pytest.raises(ValueError):
            DateRange(start_date='not-a-date', end_date='2025-01-31')

    def test_end_before_start(self):
        with pytest.raises(ValueError):
            DateRange(start_date='2025-02-01', end_date='2025-01-01')

    def test_validate_with_granularity_hourly(self):
        dr = DateRange(start_date='2025-01-01', end_date='2025-01-10')
        # Should not raise for 9-day range
        dr.validate_with_granularity('HOURLY')

    def test_validate_with_granularity_hourly_too_long(self):
        dr = DateRange(start_date='2025-01-01', end_date='2025-02-01')
        with pytest.raises(ValueError):
            dr.validate_with_granularity('HOURLY')


class TestCostExplorerDimensionKey:
    """Test the cost_explorer package DimensionKey model."""

    def test_valid_key(self):
        dk = DimensionKey(dimension_key='SERVICE')
        assert dk.dimension_key == 'SERVICE'

    def test_valid_lowercase(self):
        dk = DimensionKey(dimension_key='service')
        assert dk.dimension_key == 'service'

    def test_invalid_key(self):
        with pytest.raises(ValueError):
            DimensionKey(dimension_key='INVALID_KEY')


# ──────────────────────────── cost_explorer/validation.py ─────────────────────


class TestCostExplorerValidation:
    """Test cost_explorer validation functions."""

    def test_validate_dimension_key_valid(self):
        assert validate_dimension_key('SERVICE') == {}

    def test_validate_dimension_key_invalid(self):
        result = validate_dimension_key('INVALID')
        assert 'error' in result

    def test_validate_dimension_key_exception(self):
        result = validate_dimension_key(None)
        assert 'error' in result

    def test_validate_date_format_valid(self):
        is_valid, _ = validate_date_format('2025-01-15')
        assert is_valid

    def test_validate_date_format_invalid_format(self):
        is_valid, error = validate_date_format('01/15/2025')
        assert not is_valid

    def test_validate_date_format_invalid_date(self):
        is_valid, error = validate_date_format('2025-02-30')
        assert not is_valid

    def test_validate_date_range_valid(self):
        is_valid, _ = validate_date_range('2025-01-01', '2025-01-31')
        assert is_valid

    def test_validate_date_range_start_after_end(self):
        is_valid, error = validate_date_range('2025-02-01', '2025-01-01')
        assert not is_valid

    def test_validate_date_range_hourly_ok(self):
        is_valid, _ = validate_date_range('2025-01-01', '2025-01-10', 'HOURLY')
        assert is_valid

    def test_validate_date_range_hourly_too_long(self):
        is_valid, error = validate_date_range('2025-01-01', '2025-02-01', 'HOURLY')
        assert not is_valid

    def test_validate_match_options_valid_dimensions(self):
        assert validate_match_options(['EQUALS'], 'Dimensions') == {}

    def test_validate_match_options_invalid(self):
        result = validate_match_options(['CONTAINS'], 'Dimensions')
        assert 'error' in result

    def test_validate_match_options_unknown_type(self):
        result = validate_match_options(['EQUALS'], 'Unknown')
        assert 'error' in result

    def test_validate_match_options_tags(self):
        assert validate_match_options(['EQUALS', 'ABSENT'], 'Tags') == {}

    def test_validate_match_options_cost_categories(self):
        assert validate_match_options(['EQUALS', 'CASE_SENSITIVE'], 'CostCategories') == {}

    def test_validate_expression_dimensions(self):
        expr = {
            'Dimensions': {
                'Key': 'SERVICE',
                'Values': ['Amazon EC2'],
                'MatchOptions': ['EQUALS'],
            }
        }
        assert validate_expression(expr, '2025-01-01', '2025-01-31', 'test') == {}

    def test_validate_expression_invalid_dimension_key(self):
        expr = {
            'Dimensions': {
                'Key': 'INVALID_DIM',
                'Values': ['foo'],
                'MatchOptions': ['EQUALS'],
            }
        }
        result = validate_expression(expr, '2025-01-01', '2025-01-31', 'test')
        assert 'error' in result

    def test_validate_expression_dimensions_missing_fields(self):
        expr = {'Dimensions': {'Key': 'SERVICE'}}
        result = validate_expression(expr, '2025-01-01', '2025-01-31', 'test')
        assert 'error' in result

    def test_validate_expression_tags(self):
        expr = {
            'Tags': {
                'Key': 'Environment',
                'Values': ['prod'],
                'MatchOptions': ['EQUALS'],
            }
        }
        assert validate_expression(expr, '2025-01-01', '2025-01-31', 'test') == {}

    def test_validate_expression_tags_missing_fields(self):
        expr = {'Tags': {'Key': 'env'}}
        result = validate_expression(expr, '2025-01-01', '2025-01-31', 'test')
        assert 'error' in result

    def test_validate_expression_cost_categories(self):
        expr = {
            'CostCategories': {
                'Key': 'Team',
                'Values': ['Engineering'],
                'MatchOptions': ['EQUALS'],
            }
        }
        assert validate_expression(expr, '2025-01-01', '2025-01-31', 'test') == {}

    def test_validate_expression_cost_categories_missing_fields(self):
        expr = {'CostCategories': {'Key': 'Team'}}
        result = validate_expression(expr, '2025-01-01', '2025-01-31', 'test')
        assert 'error' in result

    def test_validate_expression_and(self):
        expr = {
            'And': [
                {'Dimensions': {'Key': 'SERVICE', 'Values': ['EC2'], 'MatchOptions': ['EQUALS']}},
                {'Dimensions': {'Key': 'REGION', 'Values': ['us-east-1'], 'MatchOptions': ['EQUALS']}},
            ]
        }
        assert validate_expression(expr, '2025-01-01', '2025-01-31', 'test') == {}

    def test_validate_expression_or(self):
        expr = {
            'Or': [
                {'Dimensions': {'Key': 'SERVICE', 'Values': ['EC2'], 'MatchOptions': ['EQUALS']}},
                {'Dimensions': {'Key': 'SERVICE', 'Values': ['S3'], 'MatchOptions': ['EQUALS']}},
            ]
        }
        assert validate_expression(expr, '2025-01-01', '2025-01-31', 'test') == {}

    def test_validate_expression_not(self):
        expr = {
            'Not': {'Dimensions': {'Key': 'SERVICE', 'Values': ['Tax'], 'MatchOptions': ['EQUALS']}}
        }
        assert validate_expression(expr, '2025-01-01', '2025-01-31', 'test') == {}

    def test_validate_expression_and_not_list(self):
        expr = {'And': 'not-a-list'}
        result = validate_expression(expr, '2025-01-01', '2025-01-31', 'test')
        assert 'error' in result

    def test_validate_expression_or_not_list(self):
        expr = {'Or': 'not-a-list'}
        result = validate_expression(expr, '2025-01-01', '2025-01-31', 'test')
        assert 'error' in result

    def test_validate_expression_not_not_dict(self):
        expr = {'Not': 'not-a-dict'}
        result = validate_expression(expr, '2025-01-01', '2025-01-31', 'test')
        assert 'error' in result

    def test_validate_expression_multiple_operators(self):
        expr = {
            'And': [{'Dimensions': {'Key': 'SERVICE', 'Values': ['EC2'], 'MatchOptions': ['EQUALS']}}],
            'Or': [{'Dimensions': {'Key': 'SERVICE', 'Values': ['S3'], 'MatchOptions': ['EQUALS']}}],
        }
        result = validate_expression(expr, '2025-01-01', '2025-01-31', 'test')
        assert 'error' in result

    def test_validate_expression_no_valid_keys(self):
        expr = {'InvalidKey': 'foo'}
        result = validate_expression(expr, '2025-01-01', '2025-01-31', 'test')
        assert 'error' in result

    def test_validate_expression_multiple_without_operator(self):
        expr = {
            'Dimensions': {'Key': 'SERVICE', 'Values': ['EC2'], 'MatchOptions': ['EQUALS']},
            'Tags': {'Key': 'env', 'Values': ['prod'], 'MatchOptions': ['EQUALS']},
        }
        result = validate_expression(expr, '2025-01-01', '2025-01-31', 'test')
        assert 'error' in result

    def test_validate_expression_invalid_date_range(self):
        result = validate_expression(
            {'Dimensions': {'Key': 'SERVICE', 'Values': ['EC2'], 'MatchOptions': ['EQUALS']}},
            'bad-date',
            '2025-01-31',
            'test',
        )
        assert 'error' in result

    def test_validate_group_by_valid(self):
        assert validate_group_by({'Type': 'DIMENSION', 'Key': 'SERVICE'}) == {}

    def test_validate_group_by_tag(self):
        assert validate_group_by({'Type': 'TAG', 'Key': 'Environment'}) == {}

    def test_validate_group_by_none(self):
        result = validate_group_by(None)
        assert 'error' in result

    def test_validate_group_by_missing_keys(self):
        result = validate_group_by({'Type': 'DIMENSION'})
        assert 'error' in result

    def test_validate_group_by_invalid_type(self):
        result = validate_group_by({'Type': 'INVALID', 'Key': 'SERVICE'})
        assert 'error' in result

    def test_validate_group_by_invalid_dimension_key(self):
        result = validate_group_by({'Type': 'DIMENSION', 'Key': 'INVALID_DIM'})
        assert 'error' in result

    def test_validate_forecast_date_range_valid(self):
        today = datetime.now(timezone.utc).date()
        start = today.strftime('%Y-%m-%d')
        end = (today + timedelta(days=30)).strftime('%Y-%m-%d')
        is_valid, _ = validate_forecast_date_range(start, end, 'MONTHLY')
        assert is_valid

    def test_validate_forecast_start_in_future(self):
        future = (datetime.now(timezone.utc) + timedelta(days=5)).date()
        end = (future + timedelta(days=30))
        is_valid, error = validate_forecast_date_range(
            future.strftime('%Y-%m-%d'), end.strftime('%Y-%m-%d'),
        )
        assert not is_valid

    def test_validate_forecast_end_not_future(self):
        today = datetime.now(timezone.utc).date()
        past = (today - timedelta(days=5))
        is_valid, error = validate_forecast_date_range(
            past.strftime('%Y-%m-%d'), today.strftime('%Y-%m-%d'),
        )
        assert not is_valid

    def test_validate_forecast_daily_too_long(self):
        today = datetime.now(timezone.utc).date()
        start = today.strftime('%Y-%m-%d')
        end = (today + timedelta(days=100)).strftime('%Y-%m-%d')
        is_valid, error = validate_forecast_date_range(start, end, 'DAILY')
        assert not is_valid

    def test_validate_comparison_date_range_valid(self):
        # Use 2 months ago which should be a complete month
        today = datetime.now(timezone.utc).date()
        # Go to first of 2 months ago
        if today.month <= 2:
            start = today.replace(year=today.year - 1, month=today.month + 10, day=1)
        else:
            start = today.replace(month=today.month - 2, day=1)
        if start.month == 12:
            end = start.replace(year=start.year + 1, month=1)
        else:
            end = start.replace(month=start.month + 1)
        is_valid, _ = validate_comparison_date_range(
            start.strftime('%Y-%m-%d'), end.strftime('%Y-%m-%d'),
        )
        assert is_valid

    def test_validate_comparison_not_first_of_month(self):
        is_valid, error = validate_comparison_date_range('2025-01-15', '2025-02-01')
        assert not is_valid

    def test_validate_comparison_end_not_first_of_month(self):
        is_valid, error = validate_comparison_date_range('2025-01-01', '2025-01-31')
        assert not is_valid

    def test_validate_comparison_not_exactly_one_month(self):
        is_valid, error = validate_comparison_date_range('2025-01-01', '2025-03-01')
        assert not is_valid


# ──────────────────────────── cost_explorer/helpers.py ────────────────────────


class TestCostExplorerHelpers:
    """Test cost_explorer helper functions."""

    def test_get_available_dimension_values_success(self):
        mock_ce = MagicMock()
        mock_ce.get_dimension_values.return_value = {
            'DimensionValues': [{'Value': 'Amazon EC2'}, {'Value': 'Amazon S3'}],
        }
        with patch(
            'cost_optimizer.cost_explorer.helpers.get_cost_explorer_client',
            return_value=mock_ce,
        ), patch(
            'cost_optimizer.cost_explorer.helpers.build_account_filter',
            return_value=None,
        ):
            result = get_available_dimension_values('SERVICE', '2025-01-01', '2025-01-31', 'test')

        assert result['values'] == ['Amazon EC2', 'Amazon S3']

    def test_get_available_dimension_values_pagination(self):
        mock_ce = MagicMock()
        mock_ce.get_dimension_values.side_effect = [
            {'DimensionValues': [{'Value': 'Amazon EC2'}], 'NextPageToken': 'token1'},
            {'DimensionValues': [{'Value': 'Amazon S3'}]},
        ]
        with patch(
            'cost_optimizer.cost_explorer.helpers.get_cost_explorer_client',
            return_value=mock_ce,
        ), patch(
            'cost_optimizer.cost_explorer.helpers.build_account_filter',
            return_value=None,
        ):
            result = get_available_dimension_values('SERVICE', '2025-01-01', '2025-01-31', 'test')

        assert result['values'] == ['Amazon EC2', 'Amazon S3']

    def test_get_available_dimension_values_error(self):
        with patch(
            'cost_optimizer.cost_explorer.helpers.get_cost_explorer_client',
            side_effect=Exception('fail'),
        ):
            result = get_available_dimension_values('SERVICE', '2025-01-01', '2025-01-31', 'test')
        assert 'error' in result

    def test_get_available_tag_values_success(self):
        mock_ce = MagicMock()
        mock_ce.get_tags.return_value = {'Tags': ['prod', 'dev']}
        with patch(
            'cost_optimizer.cost_explorer.helpers.get_cost_explorer_client',
            return_value=mock_ce,
        ), patch(
            'cost_optimizer.cost_explorer.helpers.build_account_filter',
            return_value=None,
        ):
            result = get_available_tag_values('Environment', '2025-01-01', '2025-01-31', 'test')

        assert result['values'] == ['prod', 'dev']

    def test_get_available_tag_values_error(self):
        with patch(
            'cost_optimizer.cost_explorer.helpers.get_cost_explorer_client',
            side_effect=Exception('fail'),
        ):
            result = get_available_tag_values('Env', '2025-01-01', '2025-01-31', 'test')
        assert 'error' in result

    def test_format_date_for_api_daily(self):
        assert format_date_for_api('2025-01-15', 'DAILY') == '2025-01-15'

    def test_format_date_for_api_monthly(self):
        assert format_date_for_api('2025-01-15', 'MONTHLY') == '2025-01-15'

    def test_format_date_for_api_hourly(self):
        assert format_date_for_api('2025-01-15', 'HOURLY') == '2025-01-15T00:00:00Z'

    def test_extract_group_key_dimension(self):
        selector = {'Dimensions': {'Key': 'SERVICE', 'Values': ['Amazon EC2']}}
        result = extract_group_key_from_complex_selector(
            selector, {'Type': 'DIMENSION', 'Key': 'SERVICE'},
        )
        assert result == 'Amazon EC2'

    def test_extract_group_key_tag(self):
        selector = {'Tags': {'Key': 'Environment', 'Values': ['production']}}
        result = extract_group_key_from_complex_selector(
            selector, {'Type': 'TAG', 'Key': 'Environment'},
        )
        assert result == 'production'

    def test_extract_group_key_cost_category(self):
        selector = {'CostCategories': {'Key': 'Team', 'Values': ['Engineering']}}
        result = extract_group_key_from_complex_selector(
            selector, {'Type': 'COST_CATEGORY', 'Key': 'Team'},
        )
        assert result == 'Engineering'

    def test_extract_group_key_nested_and(self):
        selector = {
            'And': [
                {'Dimensions': {'Key': 'SERVICE', 'Values': ['Amazon EC2']}},
                {'Dimensions': {'Key': 'REGION', 'Values': ['us-east-1']}},
            ]
        }
        result = extract_group_key_from_complex_selector(
            selector, {'Type': 'DIMENSION', 'Key': 'SERVICE'},
        )
        assert result == 'Amazon EC2'

    def test_extract_group_key_nested_not(self):
        selector = {
            'Not': {'Dimensions': {'Key': 'SERVICE', 'Values': ['Tax']}}
        }
        result = extract_group_key_from_complex_selector(
            selector, {'Type': 'DIMENSION', 'Key': 'SERVICE'},
        )
        assert result == 'Tax'

    def test_extract_group_key_empty_values(self):
        selector = {'Dimensions': {'Key': 'SERVICE', 'Values': ['']}}
        result = extract_group_key_from_complex_selector(
            selector, {'Type': 'DIMENSION', 'Key': 'SERVICE'},
        )
        assert result == 'No SERVICE'

    def test_extract_group_key_unknown(self):
        selector = {'SomethingElse': {}}
        result = extract_group_key_from_complex_selector(
            selector, {'Type': 'DIMENSION', 'Key': 'SERVICE'},
        )
        assert result == 'Unknown'

    def test_extract_usage_context_dimensions(self):
        selector = {
            'And': [
                {'Dimensions': {'Key': 'SERVICE', 'Values': ['Amazon EC2']}},
                {'Dimensions': {'Key': 'USAGE_TYPE', 'Values': ['BoxUsage:c5.large']}},
            ]
        }
        context = extract_usage_context_from_selector(selector)
        assert context['service'] == 'Amazon EC2'
        assert context['usage_type'] == 'BoxUsage:c5.large'

    def test_extract_usage_context_tags(self):
        selector = {'Tags': {'Key': 'Environment', 'Values': ['production']}}
        context = extract_usage_context_from_selector(selector)
        assert context['tag_environment'] == 'production'

    def test_extract_usage_context_cost_categories(self):
        selector = {'CostCategories': {'Key': 'Team', 'Values': ['Engineering']}}
        context = extract_usage_context_from_selector(selector)
        assert context['category_team'] == 'Engineering'

    def test_extract_usage_context_not_operator(self):
        selector = {'Not': {'Dimensions': {'Key': 'REGION', 'Values': ['us-east-1']}}}
        context = extract_usage_context_from_selector(selector)
        assert context['region'] == 'us-east-1'

    def test_create_detailed_group_key_simple(self):
        result = create_detailed_group_key('Amazon EC2', {}, {'Key': 'SERVICE'})
        assert result == 'Amazon EC2'

    def test_create_detailed_group_key_with_usage_type(self):
        context = {'service': 'Amazon EC2', 'usage_type': 'BoxUsage:c5.large'}
        result = create_detailed_group_key('Amazon EC2', context, {'Key': 'SERVICE'})
        assert 'BoxUsage:c5.large' in result

    def test_create_detailed_group_key_with_service_context(self):
        context = {'service': 'Amazon EC2'}
        result = create_detailed_group_key('us-east-1', context, {'Key': 'REGION'})
        assert 'Amazon EC2' in result


# ──────────────────────────── cost_explorer/metadata.py ──────────────────────


class TestCostExplorerMetadata:
    """Test cost_explorer metadata handlers."""

    @pytest.mark.asyncio
    async def test_get_dimension_values_success(self):
        mock_ce = MagicMock()
        mock_ce.get_dimension_values.return_value = {
            'DimensionValues': [{'Value': 'Amazon EC2'}, {'Value': 'Amazon S3'}],
        }

        with patch(
            'cost_optimizer.cost_explorer.metadata.get_cost_explorer_client',
            return_value=mock_ce,
        ), patch(
            'cost_optimizer.cost_explorer.metadata.build_account_filter',
            return_value=None,
        ):
            ctx = MagicMock()
            dim_key = DimensionKey(dimension_key='SERVICE')
            result = await ce_get_dimension_values(ctx, dim_key, client_id='test', date_range=None, account_scope='auto')

        assert result['dimension'] == 'SERVICE'
        assert len(result['values']) == 2

    @pytest.mark.asyncio
    async def test_get_dimension_values_with_date_range(self):
        mock_ce = MagicMock()
        mock_ce.get_dimension_values.return_value = {
            'DimensionValues': [{'Value': 'us-east-1'}],
        }

        with patch(
            'cost_optimizer.cost_explorer.metadata.get_cost_explorer_client',
            return_value=mock_ce,
        ), patch(
            'cost_optimizer.cost_explorer.metadata.build_account_filter',
            return_value=None,
        ):
            ctx = MagicMock()
            dim_key = DimensionKey(dimension_key='REGION')
            dr = DateRange(start_date='2025-01-01', end_date='2025-01-31')
            result = await ce_get_dimension_values(ctx, dim_key, client_id='test', date_range=dr, account_scope='auto')

        assert result['values'] == ['us-east-1']

    @pytest.mark.asyncio
    async def test_get_dimension_values_pagination(self):
        mock_ce = MagicMock()
        mock_ce.get_dimension_values.side_effect = [
            {'DimensionValues': [{'Value': 'Amazon EC2'}], 'NextPageToken': 'tok'},
            {'DimensionValues': [{'Value': 'Amazon S3'}]},
        ]

        with patch(
            'cost_optimizer.cost_explorer.metadata.get_cost_explorer_client',
            return_value=mock_ce,
        ), patch(
            'cost_optimizer.cost_explorer.metadata.build_account_filter',
            return_value=None,
        ):
            ctx = MagicMock()
            dim_key = DimensionKey(dimension_key='SERVICE')
            result = await ce_get_dimension_values(ctx, dim_key, client_id='test', date_range=None, account_scope='auto')

        assert len(result['values']) == 2

    @pytest.mark.asyncio
    async def test_get_dimension_values_error(self):
        with patch(
            'cost_optimizer.cost_explorer.metadata.get_cost_explorer_client',
            side_effect=Exception('fail'),
        ):
            ctx = MagicMock()
            dim_key = DimensionKey(dimension_key='SERVICE')
            result = await ce_get_dimension_values(ctx, dim_key, client_id='test', date_range=None, account_scope='auto')

        assert 'error' in result

    @pytest.mark.asyncio
    async def test_get_tag_values_success(self):
        mock_ce = MagicMock()
        mock_ce.get_tags.return_value = {'Tags': ['prod', 'dev']}

        with patch(
            'cost_optimizer.cost_explorer.metadata.get_cost_explorer_client',
            return_value=mock_ce,
        ), patch(
            'cost_optimizer.cost_explorer.metadata.build_account_filter',
            return_value=None,
        ):
            ctx = MagicMock()
            result = await ce_get_tag_values(ctx, tag_key='Environment', client_id='test', date_range=None, account_scope='auto')

        assert result['tag_key'] == 'Environment'
        assert result['values'] == ['prod', 'dev']

    @pytest.mark.asyncio
    async def test_get_tag_values_with_date_range(self):
        mock_ce = MagicMock()
        mock_ce.get_tags.return_value = {'Tags': ['v1']}

        with patch(
            'cost_optimizer.cost_explorer.metadata.get_cost_explorer_client',
            return_value=mock_ce,
        ), patch(
            'cost_optimizer.cost_explorer.metadata.build_account_filter',
            return_value=None,
        ):
            ctx = MagicMock()
            dr = DateRange(start_date='2025-01-01', end_date='2025-01-31')
            result = await ce_get_tag_values(
                ctx, tag_key='Version', client_id='test', date_range=dr, account_scope='auto',
            )

        assert result['values'] == ['v1']

    @pytest.mark.asyncio
    async def test_get_tag_values_error(self):
        with patch(
            'cost_optimizer.cost_explorer.metadata.get_cost_explorer_client',
            side_effect=Exception('fail'),
        ):
            ctx = MagicMock()
            result = await ce_get_tag_values(ctx, tag_key='Env', client_id='test', date_range=None, account_scope='auto')

        assert 'error' in result

    @pytest.mark.asyncio
    async def test_get_dimension_values_with_account_filter(self):
        mock_ce = MagicMock()
        mock_ce.get_dimension_values.return_value = {
            'DimensionValues': [{'Value': 'Amazon EC2'}],
        }
        mock_filter = {'Dimensions': {'Key': 'LINKED_ACCOUNT', 'Values': ['123']}}

        with patch(
            'cost_optimizer.cost_explorer.metadata.get_cost_explorer_client',
            return_value=mock_ce,
        ), patch(
            'cost_optimizer.cost_explorer.metadata.build_account_filter',
            return_value=mock_filter,
        ):
            ctx = MagicMock()
            dim_key = DimensionKey(dimension_key='SERVICE')
            result = await ce_get_dimension_values(ctx, dim_key, client_id='test', date_range=None, account_scope='auto')

        # Verify filter was passed
        call_kwargs = mock_ce.get_dimension_values.call_args[1]
        assert 'Filter' in call_kwargs
