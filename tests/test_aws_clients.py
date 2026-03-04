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

"""Tests for aws_clients module."""

import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch
import awslabs.cost_explorer_mcp_server.aws_clients as aws_clients


@pytest.fixture(autouse=True)
def reset_client_cache():
    """Reset the client cache before each test."""
    with aws_clients._service_client_lock:
        aws_clients._service_clients.clear()
        aws_clients._service_sessions.clear()
        aws_clients._service_token_expiration.clear()
    yield
    with aws_clients._service_client_lock:
        aws_clients._service_clients.clear()
        aws_clients._service_sessions.clear()
        aws_clients._service_token_expiration.clear()


class TestIsServiceTokenExpired:
    """Test token expiration checking."""

    def test_expired_when_not_in_cache(self):
        """Token should be considered expired if not in cache."""
        result = aws_clients._is_service_token_expired('unknown-client')
        assert result is True

    def test_not_expired_when_valid(self):
        """Token should not be expired when within buffer."""
        client_id = 'test-client'
        # Set expiration to 1 hour from now
        future_time = datetime.now(timezone.utc) + timedelta(hours=1)
        aws_clients._service_token_expiration[client_id] = future_time
        
        result = aws_clients._is_service_token_expired(client_id)
        assert result is False

    def test_expired_within_buffer(self):
        """Token should be expired when within buffer time."""
        client_id = 'test-client'
        # Set expiration to 2 minutes from now (within 5 minute buffer)
        near_future = datetime.now(timezone.utc) + timedelta(minutes=2)
        aws_clients._service_token_expiration[client_id] = near_future
        
        result = aws_clients._is_service_token_expired(client_id)
        assert result is True

    def test_expired_in_past(self):
        """Token should be expired when in the past."""
        client_id = 'test-client'
        past_time = datetime.now(timezone.utc) - timedelta(hours=1)
        aws_clients._service_token_expiration[client_id] = past_time
        
        result = aws_clients._is_service_token_expired(client_id)
        assert result is True


class TestCreateSessionWithAssumedRole:
    """Test assumed role session creation."""

    @patch('awslabs.cost_explorer_mcp_server.aws_clients._get_role_arn_for_client')
    @patch('awslabs.cost_explorer_mcp_server.aws_clients.boto3')
    def test_create_session_success(self, mock_boto3, mock_get_role):
        """Test successful session creation."""
        mock_get_role.return_value = 'arn:aws:iam::123456789012:role/test-role'
        
        # Mock the STS assume_role response
        mock_sts = MagicMock()
        mock_session = MagicMock()
        mock_assumed_session = MagicMock()
        
        expiration = datetime.now(timezone.utc) + timedelta(hours=1)
        mock_sts.assume_role.return_value = {
            'Credentials': {
                'AccessKeyId': 'AKIATEST',
                'SecretAccessKey': 'secret',
                'SessionToken': 'token',
                'Expiration': expiration,
            }
        }
        
        mock_session.client.return_value = mock_sts
        mock_boto3.Session.side_effect = [mock_session, mock_assumed_session]
        
        session, exp = aws_clients._create_assumed_session('test-client')
        
        assert session == mock_assumed_session
        assert exp == expiration

    @patch('awslabs.cost_explorer_mcp_server.aws_clients._get_role_arn_for_client')
    def test_create_session_no_role(self, mock_get_role):
        """Test error when role not found."""
        mock_get_role.return_value = None
        
        with pytest.raises(ValueError, match='not found in clients.json'):
            aws_clients._create_assumed_session('unknown-client')


class TestGetAWSClient:
    """Test AWS client factory."""

    @patch('awslabs.cost_explorer_mcp_server.aws_clients._create_assumed_session')
    def test_get_aws_client_creates_new(self, mock_create_session):
        """Test creating a new client."""
        mock_session = MagicMock()
        mock_client = MagicMock()
        mock_session.client.return_value = mock_client
        
        expiration = datetime.now(timezone.utc) + timedelta(hours=1)
        mock_create_session.return_value = (mock_session, expiration)
        
        client = aws_clients.get_aws_client('test-client', 'ec2')
        
        assert client == mock_client
        mock_session.client.assert_called_once_with('ec2', region_name='eu-west-1')

    @patch('awslabs.cost_explorer_mcp_server.aws_clients._create_assumed_session')
    def test_get_aws_client_caches(self, mock_create_session):
        """Test that clients are cached."""
        mock_session = MagicMock()
        mock_client = MagicMock()
        mock_session.client.return_value = mock_client
        
        expiration = datetime.now(timezone.utc) + timedelta(hours=1)
        mock_create_session.return_value = (mock_session, expiration)
        
        # Get client twice
        client1 = aws_clients.get_aws_client('test-client', 'ec2')
        client2 = aws_clients.get_aws_client('test-client', 'ec2')
        
        # Should only create session once
        assert mock_create_session.call_count == 1
        assert client1 == client2

    @patch('awslabs.cost_explorer_mcp_server.aws_clients._create_assumed_session')
    def test_get_aws_client_different_services(self, mock_create_session):
        """Test getting different service clients."""
        mock_session = MagicMock()
        mock_ec2 = MagicMock()
        mock_rds = MagicMock()
        
        def create_client(service, region_name=None):
            if service == 'ec2':
                return mock_ec2
            return mock_rds
        
        mock_session.client.side_effect = create_client
        
        expiration = datetime.now(timezone.utc) + timedelta(hours=1)
        mock_create_session.return_value = (mock_session, expiration)
        
        ec2_client = aws_clients.get_aws_client('test-client', 'ec2')
        rds_client = aws_clients.get_aws_client('test-client', 'rds')
        
        assert ec2_client == mock_ec2
        assert rds_client == mock_rds

    @patch('awslabs.cost_explorer_mcp_server.aws_clients._create_assumed_session')
    def test_get_aws_client_with_region(self, mock_create_session):
        """Test getting client with specific region."""
        mock_session = MagicMock()
        mock_client = MagicMock()
        mock_session.client.return_value = mock_client
        
        expiration = datetime.now(timezone.utc) + timedelta(hours=1)
        mock_create_session.return_value = (mock_session, expiration)
        
        client = aws_clients.get_aws_client('test-client', 'ec2', region='eu-west-1')
        
        mock_session.client.assert_called_once_with('ec2', region_name='eu-west-1')


class TestServiceClientHelpers:
    """Test service-specific client helpers."""

    @patch('awslabs.cost_explorer_mcp_server.aws_clients.get_aws_client')
    def test_get_ec2_client(self, mock_get_client):
        """Test EC2 client helper."""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        
        result = aws_clients.get_ec2_client('test-client', 'us-west-2')
        
        mock_get_client.assert_called_once_with('test-client', 'ec2', 'us-west-2')
        assert result == mock_client

    @patch('awslabs.cost_explorer_mcp_server.aws_clients.get_aws_client')
    def test_get_rds_client(self, mock_get_client):
        """Test RDS client helper."""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        
        result = aws_clients.get_rds_client('test-client')
        
        mock_get_client.assert_called_once_with('test-client', 'rds', None)
        assert result == mock_client

    @patch('awslabs.cost_explorer_mcp_server.aws_clients.get_aws_client')
    def test_get_cloudwatch_client(self, mock_get_client):
        """Test CloudWatch client helper."""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        
        result = aws_clients.get_cloudwatch_client('test-client')
        
        mock_get_client.assert_called_once_with('test-client', 'cloudwatch', None)
        assert result == mock_client

    @patch('awslabs.cost_explorer_mcp_server.aws_clients.get_aws_client')
    def test_get_s3_client(self, mock_get_client):
        """Test S3 client helper."""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        
        result = aws_clients.get_s3_client('test-client')
        
        mock_get_client.assert_called_once_with('test-client', 's3', None)
        assert result == mock_client

    @patch('awslabs.cost_explorer_mcp_server.aws_clients.get_aws_client')
    def test_get_elbv2_client(self, mock_get_client):
        """Test ELBv2 client helper."""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        
        result = aws_clients.get_elbv2_client('test-client')
        
        mock_get_client.assert_called_once_with('test-client', 'elbv2', None)
        assert result == mock_client

    @patch('awslabs.cost_explorer_mcp_server.aws_clients.get_aws_client')
    def test_get_elb_client(self, mock_get_client):
        """Test ELB client helper."""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        
        result = aws_clients.get_elb_client('test-client')
        
        mock_get_client.assert_called_once_with('test-client', 'elb', None)
        assert result == mock_client


class TestClearServiceClients:
    """Test client cache clearing."""

    def test_clear_service_clients(self):
        """Test clearing cached clients."""
        client_id = 'test-client'
        cache_key = f"{client_id}:ec2:us-east-1"
        
        # Add some cached data with new cache key format
        with aws_clients._service_client_lock:
            aws_clients._service_clients[cache_key] = MagicMock()
            aws_clients._service_sessions[client_id] = MagicMock()
            aws_clients._service_token_expiration[client_id] = datetime.now(timezone.utc)
        
        # Clear the cache
        aws_clients.clear_service_clients(client_id)
        
        # Verify cleared
        assert cache_key not in aws_clients._service_clients
        assert client_id not in aws_clients._service_sessions
        assert client_id not in aws_clients._service_token_expiration

    def test_clear_service_clients_nonexistent(self):
        """Test clearing non-existent client (should not error)."""
        # Should not raise any exception
        aws_clients.clear_service_clients('nonexistent-client')
