# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for validation module, specifically the VALIDATE_FILTER_VALUES flag."""

import pytest
from unittest.mock import patch, MagicMock
from awslabs.cost_explorer_mcp_server import validation
from awslabs.cost_explorer_mcp_server.validation import (
    validate_expression,
    validate_date_format,
    validate_date_range,
    validate_match_options,
    validate_group_by,
    validate_dimension_key,
    VALIDATE_FILTER_VALUES,
)


class TestValidateFilterValuesFlag:
    """Tests for the VALIDATE_FILTER_VALUES environment variable."""

    def test_validate_filter_values_default_is_false(self):
        """Test that VALIDATE_FILTER_VALUES defaults to False."""
        # The default should be False to avoid extra AWS API costs
        assert VALIDATE_FILTER_VALUES is False

    def test_validate_filter_values_env_var_parsing(self):
        """Test that the environment variable is parsed correctly."""
        import os
        
        # Test with 'true'
        with patch.dict(os.environ, {'VALIDATE_FILTER_VALUES': 'true'}):
            # Need to reload to pick up new env var
            import importlib
            importlib.reload(validation)
            assert validation.VALIDATE_FILTER_VALUES is True
        
        # Test with 'false' 
        with patch.dict(os.environ, {'VALIDATE_FILTER_VALUES': 'false'}):
            importlib.reload(validation)
            assert validation.VALIDATE_FILTER_VALUES is False
        
        # Test with 'TRUE' (case insensitive)
        with patch.dict(os.environ, {'VALIDATE_FILTER_VALUES': 'TRUE'}):
            importlib.reload(validation)
            assert validation.VALIDATE_FILTER_VALUES is True
        
        # Restore default
        with patch.dict(os.environ, {'VALIDATE_FILTER_VALUES': 'false'}):
            importlib.reload(validation)


class TestValidateExpressionWithFlagDisabled:
    """Tests for validate_expression when VALIDATE_FILTER_VALUES is False."""

    @patch.object(validation, 'VALIDATE_FILTER_VALUES', False)
    @patch('awslabs.cost_explorer_mcp_server.helpers.get_available_dimension_values')
    def test_dimensions_no_aws_call_when_flag_disabled(self, mock_get_dim_values):
        """Test that no AWS call is made for dimension validation when flag is False."""
        expression = {
            'Dimensions': {
                'Key': 'SERVICE',
                'Values': ['Amazon EC2'],
                'MatchOptions': ['EQUALS']
            }
        }
        
        result = validate_expression(expression, '2025-01-01', '2025-01-31', 'test-client')
        
        # Should NOT call AWS
        mock_get_dim_values.assert_not_called()
        # Should pass validation (no error)
        assert 'error' not in result

    @patch.object(validation, 'VALIDATE_FILTER_VALUES', False)
    @patch('awslabs.cost_explorer_mcp_server.helpers.get_available_tag_values')
    def test_tags_no_aws_call_when_flag_disabled(self, mock_get_tag_values):
        """Test that no AWS call is made for tag validation when flag is False."""
        expression = {
            'Tags': {
                'Key': 'Environment',
                'Values': ['production'],
                'MatchOptions': ['EQUALS']
            }
        }
        
        result = validate_expression(expression, '2025-01-01', '2025-01-31', 'test-client')
        
        # Should NOT call AWS
        mock_get_tag_values.assert_not_called()
        # Should pass validation
        assert 'error' not in result

    @patch.object(validation, 'VALIDATE_FILTER_VALUES', False)
    def test_invalid_dimension_key_still_validated_locally(self):
        """Test that invalid dimension keys are still caught locally."""
        expression = {
            'Dimensions': {
                'Key': 'INVALID_DIMENSION',
                'Values': ['some-value'],
                'MatchOptions': ['EQUALS']
            }
        }
        
        result = validate_expression(expression, '2025-01-01', '2025-01-31', 'test-client')
        
        # Should fail with local validation error
        assert 'error' in result
        assert 'Invalid dimension key' in result['error']


class TestValidateExpressionWithFlagEnabled:
    """Tests for validate_expression when VALIDATE_FILTER_VALUES is True."""

    @patch.object(validation, 'VALIDATE_FILTER_VALUES', True)
    @patch('awslabs.cost_explorer_mcp_server.helpers.get_available_dimension_values')
    def test_dimensions_aws_call_when_flag_enabled(self, mock_get_dim_values):
        """Test that AWS call is made for dimension validation when flag is True."""
        mock_get_dim_values.return_value = {
            'values': ['Amazon EC2', 'Amazon S3']
        }
        
        expression = {
            'Dimensions': {
                'Key': 'SERVICE',
                'Values': ['Amazon EC2'],
                'MatchOptions': ['EQUALS']
            }
        }
        
        result = validate_expression(expression, '2025-01-01', '2025-01-31', 'test-client')
        
        # Should call AWS
        mock_get_dim_values.assert_called_once_with('SERVICE', '2025-01-01', '2025-01-31', 'test-client')
        # Should pass validation
        assert 'error' not in result

    @patch.object(validation, 'VALIDATE_FILTER_VALUES', True)
    @patch('awslabs.cost_explorer_mcp_server.helpers.get_available_dimension_values')
    def test_dimensions_invalid_value_detected_when_flag_enabled(self, mock_get_dim_values):
        """Test that invalid dimension values are detected when flag is True."""
        mock_get_dim_values.return_value = {
            'values': ['Amazon EC2', 'Amazon S3']
        }
        
        expression = {
            'Dimensions': {
                'Key': 'SERVICE',
                'Values': ['NonexistentService'],
                'MatchOptions': ['EQUALS']
            }
        }
        
        result = validate_expression(expression, '2025-01-01', '2025-01-31', 'test-client')
        
        # Should fail with invalid value error
        assert 'error' in result
        assert 'Invalid value' in result['error']

    @patch.object(validation, 'VALIDATE_FILTER_VALUES', True)
    @patch('awslabs.cost_explorer_mcp_server.helpers.get_available_tag_values')
    def test_tags_aws_call_when_flag_enabled(self, mock_get_tag_values):
        """Test that AWS call is made for tag validation when flag is True."""
        mock_get_tag_values.return_value = {
            'values': ['production', 'staging', 'development']
        }
        
        expression = {
            'Tags': {
                'Key': 'Environment',
                'Values': ['production'],
                'MatchOptions': ['EQUALS']
            }
        }
        
        result = validate_expression(expression, '2025-01-01', '2025-01-31', 'test-client')
        
        # Should call AWS
        mock_get_tag_values.assert_called_once_with('Environment', '2025-01-01', '2025-01-31', 'test-client')
        # Should pass validation
        assert 'error' not in result


class TestValidateExpressionStructure:
    """Tests for expression structure validation (always performed)."""

    def test_dimensions_missing_key(self):
        """Test that missing Key in Dimensions is caught."""
        expression = {
            'Dimensions': {
                'Values': ['Amazon EC2'],
                'MatchOptions': ['EQUALS']
            }
        }
        
        result = validate_expression(expression, '2025-01-01', '2025-01-31', 'test-client')
        
        assert 'error' in result
        assert 'must include' in result['error']

    def test_dimensions_missing_values(self):
        """Test that missing Values in Dimensions is caught."""
        expression = {
            'Dimensions': {
                'Key': 'SERVICE',
                'MatchOptions': ['EQUALS']
            }
        }
        
        result = validate_expression(expression, '2025-01-01', '2025-01-31', 'test-client')
        
        assert 'error' in result
        assert 'must include' in result['error']

    def test_dimensions_missing_match_options(self):
        """Test that missing MatchOptions in Dimensions is caught."""
        expression = {
            'Dimensions': {
                'Key': 'SERVICE',
                'Values': ['Amazon EC2']
            }
        }
        
        result = validate_expression(expression, '2025-01-01', '2025-01-31', 'test-client')
        
        assert 'error' in result
        assert 'must include' in result['error']

    def test_tags_missing_key(self):
        """Test that missing Key in Tags is caught."""
        expression = {
            'Tags': {
                'Values': ['production'],
                'MatchOptions': ['EQUALS']
            }
        }
        
        result = validate_expression(expression, '2025-01-01', '2025-01-31', 'test-client')
        
        assert 'error' in result
        assert 'must include' in result['error']

    def test_invalid_match_option_dimensions(self):
        """Test that invalid MatchOptions for Dimensions is caught."""
        expression = {
            'Dimensions': {
                'Key': 'SERVICE',
                'Values': ['Amazon EC2'],
                'MatchOptions': ['INVALID_OPTION']
            }
        }
        
        result = validate_expression(expression, '2025-01-01', '2025-01-31', 'test-client')
        
        assert 'error' in result
        assert 'Invalid MatchOption' in result['error']

    def test_invalid_date_range(self):
        """Test that invalid date range is caught."""
        expression = {
            'Dimensions': {
                'Key': 'SERVICE',
                'Values': ['Amazon EC2'],
                'MatchOptions': ['EQUALS']
            }
        }
        
        # End date before start date
        result = validate_expression(expression, '2025-01-31', '2025-01-01', 'test-client')
        
        assert 'error' in result


class TestLogicalOperators:
    """Tests for logical operators (And, Or, Not)."""

    @patch.object(validation, 'VALIDATE_FILTER_VALUES', False)
    def test_and_operator(self):
        """Test And operator validation."""
        expression = {
            'And': [
                {
                    'Dimensions': {
                        'Key': 'SERVICE',
                        'Values': ['Amazon EC2'],
                        'MatchOptions': ['EQUALS']
                    }
                },
                {
                    'Dimensions': {
                        'Key': 'REGION',
                        'Values': ['us-east-1'],
                        'MatchOptions': ['EQUALS']
                    }
                }
            ]
        }
        
        result = validate_expression(expression, '2025-01-01', '2025-01-31', 'test-client')
        
        assert 'error' not in result

    @patch.object(validation, 'VALIDATE_FILTER_VALUES', False)
    def test_or_operator(self):
        """Test Or operator validation."""
        expression = {
            'Or': [
                {
                    'Dimensions': {
                        'Key': 'SERVICE',
                        'Values': ['Amazon EC2'],
                        'MatchOptions': ['EQUALS']
                    }
                },
                {
                    'Dimensions': {
                        'Key': 'SERVICE',
                        'Values': ['Amazon S3'],
                        'MatchOptions': ['EQUALS']
                    }
                }
            ]
        }
        
        result = validate_expression(expression, '2025-01-01', '2025-01-31', 'test-client')
        
        assert 'error' not in result

    @patch.object(validation, 'VALIDATE_FILTER_VALUES', False)
    def test_not_operator(self):
        """Test Not operator validation."""
        expression = {
            'Not': {
                'Dimensions': {
                    'Key': 'SERVICE',
                    'Values': ['Amazon EC2'],
                    'MatchOptions': ['EQUALS']
                }
            }
        }
        
        result = validate_expression(expression, '2025-01-01', '2025-01-31', 'test-client')
        
        assert 'error' not in result

    def test_multiple_logical_operators_error(self):
        """Test that multiple logical operators in same expression fails."""
        expression = {
            'And': [
                {'Dimensions': {'Key': 'SERVICE', 'Values': ['EC2'], 'MatchOptions': ['EQUALS']}}
            ],
            'Or': [
                {'Dimensions': {'Key': 'REGION', 'Values': ['us-east-1'], 'MatchOptions': ['EQUALS']}}
            ]
        }
        
        result = validate_expression(expression, '2025-01-01', '2025-01-31', 'test-client')
        
        assert 'error' in result
        assert 'Only one logical operator' in result['error']


class TestLocalValidationFunctions:
    """Tests for local validation functions that never call AWS."""

    def test_validate_date_format_valid(self):
        """Test valid date format."""
        is_valid, error = validate_date_format('2025-01-15')
        assert is_valid is True
        assert error == ''

    def test_validate_date_format_invalid(self):
        """Test invalid date format."""
        is_valid, error = validate_date_format('15-01-2025')
        assert is_valid is False
        assert 'not in YYYY-MM-DD format' in error

    def test_validate_date_format_invalid_date(self):
        """Test invalid date (Feb 30)."""
        is_valid, error = validate_date_format('2025-02-30')
        assert is_valid is False

    def test_validate_date_range_valid(self):
        """Test valid date range."""
        is_valid, error = validate_date_range('2025-01-01', '2025-01-31')
        assert is_valid is True
        assert error == ''

    def test_validate_date_range_invalid_order(self):
        """Test date range with end before start."""
        is_valid, error = validate_date_range('2025-01-31', '2025-01-01')
        assert is_valid is False
        assert 'cannot be after' in error

    def test_validate_date_range_hourly_limit(self):
        """Test hourly granularity 14-day limit."""
        is_valid, error = validate_date_range('2025-01-01', '2025-01-20', 'HOURLY')
        assert is_valid is False
        assert 'maximum of 14 days' in error

    def test_validate_match_options_valid(self):
        """Test valid match options."""
        result = validate_match_options(['EQUALS'], 'Dimensions')
        assert 'error' not in result

    def test_validate_match_options_invalid(self):
        """Test invalid match options."""
        result = validate_match_options(['INVALID'], 'Dimensions')
        assert 'error' in result

    def test_validate_group_by_valid(self):
        """Test valid group_by."""
        result = validate_group_by({'Type': 'DIMENSION', 'Key': 'SERVICE'})
        assert 'error' not in result

    def test_validate_group_by_invalid_type(self):
        """Test invalid group_by type."""
        result = validate_group_by({'Type': 'INVALID', 'Key': 'SERVICE'})
        assert 'error' in result

    def test_validate_group_by_invalid_dimension_key(self):
        """Test invalid dimension key for group_by."""
        result = validate_group_by({'Type': 'DIMENSION', 'Key': 'INVALID_KEY'})
        assert 'error' in result

    def test_validate_dimension_key_valid(self):
        """Test valid dimension key."""
        result = validate_dimension_key('SERVICE')
        assert 'error' not in result

    def test_validate_dimension_key_invalid(self):
        """Test invalid dimension key."""
        result = validate_dimension_key('INVALID_DIMENSION')
        assert 'error' in result
