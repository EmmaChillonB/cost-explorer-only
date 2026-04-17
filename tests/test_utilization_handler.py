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

"""Tests for utilization module."""

import pytest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
from cost_optimizer.utilization import (
    get_ec2_utilization,
    get_rds_utilization,
    get_elb_utilization,
    get_nat_gateway_utilization,
    get_ebs_utilization,
    get_multi_resource_utilization,
)
from cost_optimizer.utilization.ec2 import _assess_ec2_utilization
from cost_optimizer.utilization.rds import _assess_rds_utilization
from cost_optimizer.utilization.elb import _assess_elb_utilization
from cost_optimizer.utilization.common import (
    get_metric_statistics,
    calculate_time_range,
)


class TestCalculateTimeRange:
    """Test time range calculation."""

    def test_calculate_time_range_default(self):
        """Test default 7 day range."""
        start, end = calculate_time_range()
        diff = end - start
        assert diff.days == 7

    def test_calculate_time_range_custom(self):
        """Test custom day range."""
        start, end = calculate_time_range(days_back=14)
        diff = end - start
        assert diff.days == 14


class TestAssessEC2Utilization:
    """Test EC2 utilization assessment."""

    def test_assess_significantly_underutilized(self):
        """Test assessment for very low CPU."""
        metrics = {
            'cpu': {
                'summary': {
                    'overall_average': 2.5,
                    'overall_maximum': 10.0,
                }
            }
        }
        result = _assess_ec2_utilization(metrics)
        assert result['status'] == 'significantly_underutilized'
        assert len(result['recommendations']) > 0

    def test_assess_underutilized(self):
        """Test assessment for moderately low CPU."""
        metrics = {
            'cpu': {
                'summary': {
                    'overall_average': 15.0,
                    'overall_maximum': 40.0,
                }
            }
        }
        result = _assess_ec2_utilization(metrics)
        assert result['status'] == 'underutilized'

    def test_assess_highly_utilized(self):
        """Test assessment for high CPU."""
        metrics = {
            'cpu': {
                'summary': {
                    'overall_average': 85.0,
                    'overall_maximum': 98.0,
                }
            }
        }
        result = _assess_ec2_utilization(metrics)
        assert result['status'] == 'highly_utilized'

    def test_assess_appropriately_sized(self):
        """Test assessment for normal CPU."""
        metrics = {
            'cpu': {
                'summary': {
                    'overall_average': 50.0,
                    'overall_maximum': 75.0,
                }
            }
        }
        result = _assess_ec2_utilization(metrics)
        assert result['status'] == 'appropriately_sized'

    def test_assess_low_cpu_credits(self):
        """Test recommendation for low CPU credits."""
        metrics = {
            'cpu': {
                'summary': {
                    'overall_average': 50.0,
                }
            },
            'cpu_credit_balance': {
                'summary': {
                    'overall_average': 5.0,
                }
            }
        }
        result = _assess_ec2_utilization(metrics)
        assert any('credit' in r.lower() for r in result['recommendations'])


class TestAssessRDSUtilization:
    """Test RDS utilization assessment."""

    def test_assess_rds_underutilized(self):
        """Test assessment for underutilized RDS."""
        metrics = {
            'cpu': {
                'summary': {
                    'overall_average': 5.0,
                }
            },
            'connections': {
                'summary': {
                    'overall_average': 2.0,
                    'overall_maximum': 5.0,
                }
            },
            'free_storage_space': {
                'summary': {
                    'overall_minimum': 200 * (1024 ** 3),  # 200 GB
                }
            }
        }
        result = _assess_rds_utilization(metrics)
        assert result['status'] == 'underutilized'
        # Should have recommendations about low connections and storage
        assert len(result['recommendations']) >= 2

    def test_assess_rds_highly_utilized(self):
        """Test assessment for highly utilized RDS."""
        metrics = {
            'cpu': {
                'summary': {
                    'overall_average': 90.0,
                }
            },
            'connections': {
                'summary': {
                    'overall_average': 100.0,
                }
            }
        }
        result = _assess_rds_utilization(metrics)
        assert result['status'] == 'highly_utilized'


class TestAssessELBUtilization:
    """Test ELB utilization assessment."""

    def test_assess_elb_unused(self):
        """Test assessment for unused load balancer."""
        metrics = {
            'request_count': {
                'datapoints': [
                    {'Sum': 0},
                    {'Sum': 0},
                ]
            }
        }
        result = _assess_elb_utilization(metrics, 'application')
        assert result['status'] == 'unused'

    def test_assess_elb_low_traffic(self):
        """Test assessment for low traffic load balancer."""
        metrics = {
            'request_count': {
                'datapoints': [
                    {'Sum': 100},
                    {'Sum': 200},
                    {'Sum': 150},
                ]
            }
        }
        result = _assess_elb_utilization(metrics, 'application')
        assert result['status'] == 'low_traffic'

    def test_assess_elb_active(self):
        """Test assessment for active load balancer."""
        metrics = {
            'request_count': {
                'datapoints': [
                    {'Sum': 5000},
                    {'Sum': 6000},
                    {'Sum': 7000},
                ]
            }
        }
        result = _assess_elb_utilization(metrics, 'application')
        assert result['status'] == 'active'


class TestGetEC2Utilization:
    """Test EC2 utilization retrieval."""

    @pytest.fixture
    def mock_cloudwatch_client(self):
        """Mock CloudWatch client."""
        with patch(
            'cost_optimizer.utilization.ec2.get_cloudwatch_client'
        ) as mock:
            client = MagicMock()
            mock.return_value = client
            yield client

    @pytest.mark.asyncio
    async def test_get_ec2_utilization_success(self, mock_cloudwatch_client):
        """Test successful EC2 utilization retrieval."""
        mock_cloudwatch_client.get_metric_statistics.return_value = {
            'Datapoints': [
                {
                    'Timestamp': datetime(2025, 1, 15, 10, 0, tzinfo=timezone.utc),
                    'Average': 45.5,
                    'Maximum': 60.0,
                    'Minimum': 30.0,
                }
            ]
        }

        ctx = MagicMock()
        result = await get_ec2_utilization(
            ctx,
            client_id='test-client',
            instance_id='i-1234567890abcdef0',
            region=None,
            days_back=7,
            period_seconds=3600,
            include_memory=False,
            include_disk=False,
        )

        assert 'metrics' in result
        assert 'cpu' in result['metrics']
        assert 'assessment' in result
        assert result['instance_id'] == 'i-1234567890abcdef0'

    @pytest.mark.asyncio
    async def test_get_ec2_utilization_with_memory(self, mock_cloudwatch_client):
        """Test EC2 utilization with memory metrics."""
        mock_cloudwatch_client.get_metric_statistics.return_value = {
            'Datapoints': [
                {
                    'Timestamp': datetime(2025, 1, 15, 10, 0, tzinfo=timezone.utc),
                    'Average': 50.0,
                }
            ]
        }

        ctx = MagicMock()
        result = await get_ec2_utilization(
            ctx,
            client_id='test-client',
            instance_id='i-1234567890abcdef0',
            region=None,
            days_back=7,
            period_seconds=3600,
            include_memory=True,
            include_disk=False,
        )

        assert 'memory_used_percent' in result['metrics']

    @pytest.mark.asyncio
    async def test_get_ec2_utilization_error(self, mock_cloudwatch_client):
        """Test error handling in EC2 utilization."""
        mock_cloudwatch_client.get_metric_statistics.side_effect = Exception('CloudWatch Error')

        ctx = MagicMock()
        result = await get_ec2_utilization(
            ctx,
            client_id='test-client',
            instance_id='i-1234567890abcdef0',
            region=None,
            days_back=7,
            period_seconds=3600,
            include_memory=False,
            include_disk=False,
        )

        # Should return error in metrics
        assert 'cpu' in result['metrics']
        assert 'error' in result['metrics']['cpu']


class TestGetRDSUtilization:
    """Test RDS utilization retrieval."""

    @pytest.fixture
    def mock_cloudwatch_client(self):
        """Mock CloudWatch client."""
        with patch(
            'cost_optimizer.utilization.rds.get_cloudwatch_client'
        ) as mock:
            client = MagicMock()
            mock.return_value = client
            yield client

    @pytest.mark.asyncio
    async def test_get_rds_utilization_success(self, mock_cloudwatch_client):
        """Test successful RDS utilization retrieval."""
        mock_cloudwatch_client.get_metric_statistics.return_value = {
            'Datapoints': [
                {
                    'Timestamp': datetime(2025, 1, 15, 10, 0, tzinfo=timezone.utc),
                    'Average': 30.0,
                    'Maximum': 50.0,
                    'Minimum': 10.0,
                }
            ]
        }

        ctx = MagicMock()
        result = await get_rds_utilization(
            ctx,
            client_id='test-client',
            db_instance_identifier='my-database',
            region=None,
            days_back=7,
            period_seconds=3600,
        )

        assert 'metrics' in result
        assert 'cpu' in result['metrics']
        assert 'connections' in result['metrics']
        assert 'free_storage_space' in result['metrics']
        assert result['db_instance_identifier'] == 'my-database'


class TestGetELBUtilization:
    """Test ELB utilization retrieval."""

    @pytest.fixture
    def mock_cloudwatch_client(self):
        """Mock CloudWatch client."""
        with patch(
            'cost_optimizer.utilization.elb.get_cloudwatch_client'
        ) as mock:
            client = MagicMock()
            mock.return_value = client
            yield client

    @pytest.mark.asyncio
    async def test_get_elb_utilization_alb(self, mock_cloudwatch_client):
        """Test ALB utilization retrieval."""
        mock_cloudwatch_client.get_metric_statistics.return_value = {
            'Datapoints': [
                {
                    'Timestamp': datetime(2025, 1, 15, 10, 0, tzinfo=timezone.utc),
                    'Sum': 1000,
                    'Average': 100.0,
                }
            ]
        }

        ctx = MagicMock()
        result = await get_elb_utilization(
            ctx,
            client_id='test-client',
            load_balancer_name='app/my-alb/1234567890',
            load_balancer_type='application',
            region=None,
            days_back=7,
            period_seconds=3600,
        )

        assert 'metrics' in result
        assert 'request_count' in result['metrics']
        assert result['load_balancer_type'] == 'application'

    @pytest.mark.asyncio
    async def test_get_elb_utilization_nlb(self, mock_cloudwatch_client):
        """Test NLB utilization retrieval."""
        mock_cloudwatch_client.get_metric_statistics.return_value = {
            'Datapoints': [
                {
                    'Timestamp': datetime(2025, 1, 15, 10, 0, tzinfo=timezone.utc),
                    'Sum': 5000,
                    'Average': 50.0,
                }
            ]
        }

        ctx = MagicMock()
        result = await get_elb_utilization(
            ctx,
            client_id='test-client',
            load_balancer_name='net/my-nlb/1234567890',
            load_balancer_type='network',
            region=None,
            days_back=7,
            period_seconds=3600,
        )

        assert 'active_flow_count' in result['metrics']


class TestGetNATGatewayUtilization:
    """Test NAT Gateway utilization retrieval."""

    @pytest.fixture
    def mock_cloudwatch_client(self):
        """Mock CloudWatch client."""
        with patch(
            'cost_optimizer.utilization.network.get_cloudwatch_client'
        ) as mock:
            client = MagicMock()
            mock.return_value = client
            yield client

    @pytest.mark.asyncio
    async def test_get_nat_gateway_utilization_success(self, mock_cloudwatch_client):
        """Test successful NAT Gateway utilization retrieval."""
        mock_cloudwatch_client.get_metric_statistics.return_value = {
            'Datapoints': [
                {
                    'Timestamp': datetime(2025, 1, 15, 10, 0, tzinfo=timezone.utc),
                    'Sum': 1073741824,  # 1 GB
                    'Average': 1000000.0,
                }
            ]
        }

        ctx = MagicMock()
        result = await get_nat_gateway_utilization(
            ctx,
            client_id='test-client',
            nat_gateway_id='nat-1234567890abcdef0',
            region=None,
            days_back=7,
            period_seconds=3600,
        )

        assert 'metrics' in result
        assert 'assessment' in result
        assert 'estimated_data_processing_cost_usd' in result['assessment']


class TestGetEBSUtilization:
    """Test EBS volume utilization retrieval."""

    @pytest.fixture
    def mock_cloudwatch_client(self):
        """Mock CloudWatch client."""
        with patch(
            'cost_optimizer.utilization.ebs.get_cloudwatch_client'
        ) as mock:
            client = MagicMock()
            mock.return_value = client
            yield client

    @pytest.mark.asyncio
    async def test_get_ebs_utilization_active(self, mock_cloudwatch_client):
        """Test EBS utilization for active volume."""
        mock_cloudwatch_client.get_metric_statistics.return_value = {
            'Datapoints': [
                {
                    'Timestamp': datetime(2025, 1, 15, 10, 0, tzinfo=timezone.utc),
                    'Sum': 10000,
                    'Average': 100.0,
                }
            ]
        }

        ctx = MagicMock()
        result = await get_ebs_utilization(
            ctx,
            client_id='test-client',
            volume_id='vol-1234567890abcdef0',
            region=None,
            days_back=7,
            period_seconds=3600,
        )

        assert 'metrics' in result
        assert 'read_ops' in result['metrics']
        assert 'write_ops' in result['metrics']

    @pytest.mark.asyncio
    async def test_get_ebs_utilization_idle(self, mock_cloudwatch_client):
        """Test EBS utilization for idle volume."""
        mock_cloudwatch_client.get_metric_statistics.return_value = {
            'Datapoints': []
        }

        ctx = MagicMock()
        result = await get_ebs_utilization(
            ctx,
            client_id='test-client',
            volume_id='vol-1234567890abcdef0',
            region=None,
            days_back=7,
            period_seconds=3600,
        )

        assert result['assessment']['status'] == 'idle'


class TestGetMultiResourceUtilization:
    """Test multi-resource utilization retrieval."""

    @pytest.mark.asyncio
    async def test_get_multi_resource_utilization(self):
        """Test multi-resource utilization overview."""
        with patch('cost_optimizer.utilization.multi.get_ec2_client') as mock_ec2, \
             patch('cost_optimizer.utilization.ec2.get_cloudwatch_client') as mock_cw, \
             patch('cost_optimizer.utilization.multi.get_rds_client') as mock_rds:

            ec2_client = MagicMock()
            cw_client = MagicMock()
            rds_client = MagicMock()

            mock_ec2.return_value = ec2_client
            mock_cw.return_value = cw_client
            mock_rds.return_value = rds_client

            # Mock EC2 instances with all required fields
            ec2_paginator = MagicMock()
            ec2_client.get_paginator.return_value = ec2_paginator
            ec2_paginator.paginate.return_value = [
                {
                    'Reservations': [
                        {
                            'Instances': [
                                {
                                    'InstanceId': 'i-123',
                                    'InstanceType': 't3.micro',
                                    'Tags': [{'Key': 'Name', 'Value': 'test'}],
                                    'State': {'Name': 'running'},
                                    'LaunchTime': datetime(2025, 1, 1, tzinfo=timezone.utc),
                                }
                            ]
                        }
                    ]
                }
            ]

            # Mock RDS instances
            rds_paginator = MagicMock()
            rds_client.get_paginator.return_value = rds_paginator
            rds_paginator.paginate.return_value = [
                {
                    'DBInstances': [
                        {
                            'DBInstanceIdentifier': 'db-123',
                            'DBInstanceClass': 'db.t3.micro',
                            'Engine': 'mysql',
                            'MultiAZ': False,
                            'DBInstanceStatus': 'available',
                        }
                    ]
                }
            ]

            # Mock CloudWatch metrics
            cw_client.get_metric_statistics.return_value = {
                'Datapoints': [
                    {
                        'Timestamp': datetime(2025, 1, 15, 10, 0, tzinfo=timezone.utc),
                        'Average': 5.0,
                        'Maximum': 10.0,
                        'Minimum': 2.0,
                    }
                ]
            }

            ctx = MagicMock()
            result = await get_multi_resource_utilization(
                ctx,
                client_id='test-client',
                region='eu-west-1',
                days_back=7,
                include_ec2=True,
                include_rds=True,
                ec2_filters=None,
            )

            assert 'ec2' in result
            assert 'rds' in result
            assert result['ec2']['total_instances'] == 1
            assert result['rds']['total_instances'] == 1


class TestGetMetricStatistics:
    """Test CloudWatch metric statistics helper."""

    def test_get_metric_statistics_success(self):
        """Test successful metric retrieval."""
        mock_client = MagicMock()
        mock_client.get_metric_statistics.return_value = {
            'Datapoints': [
                {
                    'Timestamp': datetime(2025, 1, 15, 10, 0, tzinfo=timezone.utc),
                    'Average': 50.0,
                    'Maximum': 80.0,
                    'Minimum': 20.0,
                },
                {
                    'Timestamp': datetime(2025, 1, 15, 11, 0, tzinfo=timezone.utc),
                    'Average': 60.0,
                    'Maximum': 90.0,
                    'Minimum': 30.0,
                }
            ]
        }

        start = datetime(2025, 1, 15, tzinfo=timezone.utc)
        end = datetime(2025, 1, 16, tzinfo=timezone.utc)
        
        result = get_metric_statistics(
            mock_client,
            'AWS/EC2',
            'CPUUtilization',
            [{'Name': 'InstanceId', 'Value': 'i-123'}],
            start,
            end,
        )

        assert result['metric_name'] == 'CPUUtilization'
        assert len(result['datapoints']) == 2
        assert result['summary']['overall_average'] == 55.0  # (50 + 60) / 2
        assert result['summary']['overall_maximum'] == 90.0
        assert result['summary']['overall_minimum'] == 20.0

    def test_get_metric_statistics_empty(self):
        """Test empty metric response."""
        mock_client = MagicMock()
        mock_client.get_metric_statistics.return_value = {
            'Datapoints': []
        }

        start = datetime(2025, 1, 15, tzinfo=timezone.utc)
        end = datetime(2025, 1, 16, tzinfo=timezone.utc)
        
        result = get_metric_statistics(
            mock_client,
            'AWS/EC2',
            'CPUUtilization',
            [{'Name': 'InstanceId', 'Value': 'i-123'}],
            start,
            end,
        )

        assert result['summary']['datapoint_count'] == 0
        assert result['summary']['overall_average'] is None

    def test_get_metric_statistics_error(self):
        """Test error handling in metric retrieval."""
        mock_client = MagicMock()
        mock_client.get_metric_statistics.side_effect = Exception('CloudWatch Error')

        start = datetime(2025, 1, 15, tzinfo=timezone.utc)
        end = datetime(2025, 1, 16, tzinfo=timezone.utc)
        
        result = get_metric_statistics(
            mock_client,
            'AWS/EC2',
            'CPUUtilization',
            [{'Name': 'InstanceId', 'Value': 'i-123'}],
            start,
            end,
        )

        assert 'error' in result
