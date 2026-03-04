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

"""Tests for inventory module."""

import pytest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch, PropertyMock
from awslabs.cost_explorer_mcp_server.inventory import (
    describe_ec2_instances,
    describe_rds_instances,
    describe_ebs_volumes,
    describe_ebs_snapshots,
    describe_load_balancers,
    describe_nat_gateways,
    describe_elastic_ips,
    list_s3_buckets,
)
from awslabs.cost_explorer_mcp_server.inventory.common import serialize_datetime


class TestSerializeDatetime:
    """Test datetime serialization helper."""

    def test_serialize_datetime_object(self):
        """Test serialization of datetime object."""
        dt = datetime(2025, 1, 15, 10, 30, 0, tzinfo=timezone.utc)
        result = serialize_datetime(dt)
        assert result == '2025-01-15T10:30:00+00:00'

    def test_serialize_dict_with_datetime(self):
        """Test serialization of dict containing datetime."""
        data = {
            'name': 'test',
            'created': datetime(2025, 1, 15, 10, 30, 0, tzinfo=timezone.utc),
        }
        result = serialize_datetime(data)
        assert result['name'] == 'test'
        assert result['created'] == '2025-01-15T10:30:00+00:00'

    def test_serialize_list_with_datetime(self):
        """Test serialization of list containing datetime."""
        data = [
            datetime(2025, 1, 15, tzinfo=timezone.utc),
            'string',
            123,
        ]
        result = serialize_datetime(data)
        assert result[0] == '2025-01-15T00:00:00+00:00'
        assert result[1] == 'string'
        assert result[2] == 123


class TestDescribeEC2Instances:
    """Test EC2 instance inventory functionality."""

    @pytest.fixture
    def mock_ec2_client(self):
        """Mock EC2 client."""
        with patch(
            'awslabs.cost_explorer_mcp_server.inventory.ec2.get_ec2_client'
        ) as mock:
            client = MagicMock()
            mock.return_value = client
            yield client

    @pytest.mark.asyncio
    async def test_describe_ec2_instances_success(self, mock_ec2_client):
        """Test successful EC2 instance description."""
        # Setup mock paginator
        mock_paginator = MagicMock()
        mock_ec2_client.get_paginator.return_value = mock_paginator
        mock_paginator.paginate.return_value = [
            {
                'Reservations': [
                    {
                        'Instances': [
                            {
                                'InstanceId': 'i-1234567890abcdef0',
                                'InstanceType': 't3.medium',
                                'State': {'Name': 'running'},
                                'LaunchTime': datetime(2025, 1, 1, tzinfo=timezone.utc),
                                'Platform': 'linux',
                                'Placement': {'AvailabilityZone': 'us-east-1a'},
                                'PrivateIpAddress': '10.0.0.1',
                                'VpcId': 'vpc-123',
                                'SubnetId': 'subnet-123',
                                'Architecture': 'x86_64',
                                'RootDeviceType': 'ebs',
                                'Tags': [{'Key': 'Name', 'Value': 'test-instance'}],
                                'EbsOptimized': False,
                                'BlockDeviceMappings': [
                                    {
                                        'DeviceName': '/dev/xvda',
                                        'Ebs': {
                                            'VolumeId': 'vol-123',
                                            'DeleteOnTermination': True,
                                        },
                                    }
                                ],
                            }
                        ]
                    }
                ]
            }
        ]

        ctx = MagicMock()
        result = await describe_ec2_instances(
            ctx,
            client_id='test-client',
            region='us-east-1',
        )

        assert 'instances' in result
        assert result['count'] == 1
        assert result['instances'][0]['InstanceId'] == 'i-1234567890abcdef0'
        assert result['instances'][0]['InstanceType'] == 't3.medium'
        assert result['instances'][0]['State'] == 'running'
        assert result['instances'][0]['Tags'] == {'Name': 'test-instance'}

    @pytest.mark.asyncio
    async def test_describe_ec2_instances_with_filters(self, mock_ec2_client):
        """Test EC2 instance description with filters."""
        mock_paginator = MagicMock()
        mock_ec2_client.get_paginator.return_value = mock_paginator
        mock_paginator.paginate.return_value = [{'Reservations': []}]

        ctx = MagicMock()
        filters = [{'Name': 'instance-state-name', 'Values': ['running']}]
        
        result = await describe_ec2_instances(
            ctx,
            client_id='test-client',
            filters=filters,
        )

        mock_paginator.paginate.assert_called_once()
        call_kwargs = mock_paginator.paginate.call_args[1]
        assert call_kwargs['Filters'] == filters

    @pytest.mark.asyncio
    async def test_describe_ec2_instances_error(self, mock_ec2_client):
        """Test error handling in EC2 description."""
        mock_ec2_client.get_paginator.side_effect = Exception('AWS Error')

        ctx = MagicMock()
        result = await describe_ec2_instances(ctx, client_id='test-client')

        assert 'error' in result
        assert 'AWS Error' in result['error']


class TestDescribeRDSInstances:
    """Test RDS instance inventory functionality."""

    @pytest.fixture
    def mock_rds_client(self):
        """Mock RDS client."""
        with patch(
            'awslabs.cost_explorer_mcp_server.inventory.rds.get_rds_client'
        ) as mock:
            client = MagicMock()
            mock.return_value = client
            yield client

    @pytest.mark.asyncio
    async def test_describe_rds_instances_success(self, mock_rds_client):
        """Test successful RDS instance description."""
        mock_paginator = MagicMock()
        mock_rds_client.get_paginator.return_value = mock_paginator
        mock_paginator.paginate.return_value = [
            {
                'DBInstances': [
                    {
                        'DBInstanceIdentifier': 'my-database',
                        'DBInstanceClass': 'db.t3.medium',
                        'Engine': 'mysql',
                        'EngineVersion': '8.0.32',
                        'DBInstanceStatus': 'available',
                        'AllocatedStorage': 100,
                        'StorageType': 'gp3',
                        'MultiAZ': True,
                        'AvailabilityZone': 'us-east-1a',
                        'PubliclyAccessible': False,
                        'StorageEncrypted': True,
                        'InstanceCreateTime': datetime(2025, 1, 1, tzinfo=timezone.utc),
                        'BackupRetentionPeriod': 7,
                        'AutoMinorVersionUpgrade': True,
                        'LicenseModel': 'general-public-license',
                        'DeletionProtection': True,
                        'PerformanceInsightsEnabled': True,
                        'TagList': [{'Key': 'Environment', 'Value': 'prod'}],
                    }
                ]
            }
        ]

        ctx = MagicMock()
        result = await describe_rds_instances(ctx, client_id='test-client')

        assert 'db_instances' in result
        assert result['count'] == 1
        assert result['db_instances'][0]['DBInstanceIdentifier'] == 'my-database'
        assert result['db_instances'][0]['DBInstanceClass'] == 'db.t3.medium'
        assert result['db_instances'][0]['MultiAZ'] is True

    @pytest.mark.asyncio
    async def test_describe_rds_instances_error(self, mock_rds_client):
        """Test error handling in RDS description."""
        mock_rds_client.get_paginator.side_effect = Exception('RDS Error')

        ctx = MagicMock()
        result = await describe_rds_instances(ctx, client_id='test-client')

        assert 'error' in result


class TestDescribeEBSVolumes:
    """Test EBS volume inventory functionality."""

    @pytest.fixture
    def mock_ec2_client(self):
        """Mock EC2 client for EBS."""
        with patch(
            'awslabs.cost_explorer_mcp_server.inventory.ebs.get_ec2_client'
        ) as mock:
            client = MagicMock()
            mock.return_value = client
            yield client

    @pytest.mark.asyncio
    async def test_describe_ebs_volumes_success(self, mock_ec2_client):
        """Test successful EBS volume description."""
        mock_paginator = MagicMock()
        mock_ec2_client.get_paginator.return_value = mock_paginator
        mock_paginator.paginate.return_value = [
            {
                'Volumes': [
                    {
                        'VolumeId': 'vol-123',
                        'Size': 100,
                        'VolumeType': 'gp3',
                        'State': 'in-use',
                        'AvailabilityZone': 'us-east-1a',
                        'CreateTime': datetime(2025, 1, 1, tzinfo=timezone.utc),
                        'Encrypted': True,
                        'Iops': 3000,
                        'Throughput': 125,
                        'Attachments': [
                            {
                                'InstanceId': 'i-123',
                                'Device': '/dev/xvda',
                                'State': 'attached',
                                'DeleteOnTermination': True,
                            }
                        ],
                        'Tags': [{'Key': 'Name', 'Value': 'data-volume'}],
                    },
                    {
                        'VolumeId': 'vol-456',
                        'Size': 50,
                        'VolumeType': 'gp2',
                        'State': 'available',
                        'AvailabilityZone': 'us-east-1a',
                        'CreateTime': datetime(2025, 1, 1, tzinfo=timezone.utc),
                        'Encrypted': False,
                        'Attachments': [],
                        'Tags': [],
                    }
                ]
            }
        ]

        ctx = MagicMock()
        result = await describe_ebs_volumes(
            ctx, 
            client_id='test-client',
            region=None,
            volume_ids=None,
            filters=None,
            include_unattached_only=False,
        )

        assert 'volumes' in result
        assert result['count'] == 2
        assert result['unattached_count'] == 1
        assert result['total_size_gb'] == 150
        assert result['unattached_size_gb'] == 50

    @pytest.mark.asyncio
    async def test_describe_ebs_volumes_unattached_only(self, mock_ec2_client):
        """Test filtering for unattached volumes only."""
        mock_paginator = MagicMock()
        mock_ec2_client.get_paginator.return_value = mock_paginator
        mock_paginator.paginate.return_value = [{'Volumes': []}]

        ctx = MagicMock()
        result = await describe_ebs_volumes(
            ctx,
            client_id='test-client',
            region=None,
            volume_ids=None,
            filters=None,
            include_unattached_only=True,
        )

        call_kwargs = mock_paginator.paginate.call_args[1]
        assert {'Name': 'status', 'Values': ['available']} in call_kwargs['Filters']


class TestDescribeEBSSnapshots:
    """Test EBS snapshot inventory functionality."""

    @pytest.fixture
    def mock_ec2_client(self):
        """Mock EC2 client for snapshots."""
        with patch(
            'awslabs.cost_explorer_mcp_server.inventory.ebs.get_ec2_client'
        ) as mock:
            client = MagicMock()
            mock.return_value = client
            yield client

    @pytest.mark.asyncio
    async def test_describe_ebs_snapshots_orphan_detection(self, mock_ec2_client):
        """Test orphan snapshot detection."""
        # Mock volume paginator
        vol_paginator = MagicMock()
        vol_paginator.paginate.return_value = [
            {'Volumes': [{'VolumeId': 'vol-123'}]}  # Only vol-123 exists
        ]
        
        # Mock snapshot paginator
        snap_paginator = MagicMock()
        snap_paginator.paginate.return_value = [
            {
                'Snapshots': [
                    {
                        'SnapshotId': 'snap-123',
                        'VolumeId': 'vol-123',  # Exists
                        'VolumeSize': 100,
                        'State': 'completed',
                        'StartTime': datetime(2025, 1, 1, tzinfo=timezone.utc),
                        'Encrypted': False,
                        'Tags': [],
                    },
                    {
                        'SnapshotId': 'snap-456',
                        'VolumeId': 'vol-deleted',  # Doesn't exist - orphaned
                        'VolumeSize': 50,
                        'State': 'completed',
                        'StartTime': datetime(2025, 1, 1, tzinfo=timezone.utc),
                        'Encrypted': False,
                        'Tags': [],
                    }
                ]
            }
        ]
        
        def get_paginator(operation):
            if operation == 'describe_volumes':
                return vol_paginator
            return snap_paginator
        
        mock_ec2_client.get_paginator.side_effect = get_paginator

        ctx = MagicMock()
        result = await describe_ebs_snapshots(
            ctx, 
            client_id='test-client',
            region=None,
            owner_ids=None,
            snapshot_ids=None,
            include_orphaned_only=False,
        )

        assert result['count'] == 2
        assert result['orphaned_count'] == 1
        assert result['orphaned_size_gb'] == 50

    @pytest.mark.asyncio
    async def test_describe_ebs_snapshots_orphaned_only(self, mock_ec2_client):
        """Test filtering for orphaned snapshots only."""
        vol_paginator = MagicMock()
        vol_paginator.paginate.return_value = [{'Volumes': []}]
        
        snap_paginator = MagicMock()
        snap_paginator.paginate.return_value = [
            {
                'Snapshots': [
                    {
                        'SnapshotId': 'snap-orphan',
                        'VolumeId': 'vol-deleted',
                        'VolumeSize': 100,
                        'State': 'completed',
                        'StartTime': datetime(2025, 1, 1, tzinfo=timezone.utc),
                        'Encrypted': False,
                        'Tags': [],
                    }
                ]
            }
        ]
        
        def get_paginator(operation):
            if operation == 'describe_volumes':
                return vol_paginator
            return snap_paginator
        
        mock_ec2_client.get_paginator.side_effect = get_paginator

        ctx = MagicMock()
        result = await describe_ebs_snapshots(
            ctx,
            client_id='test-client',
            region=None,
            owner_ids=None,
            snapshot_ids=None,
            include_orphaned_only=True,
        )

        assert result['count'] == 1
        assert result['orphaned_count'] == 1


class TestDescribeLoadBalancers:
    """Test load balancer inventory functionality."""

    @pytest.fixture
    def mock_elbv2_client(self):
        """Mock ELBv2 client."""
        with patch(
            'awslabs.cost_explorer_mcp_server.inventory.elb.get_elbv2_client'
        ) as mock:
            client = MagicMock()
            mock.return_value = client
            yield client

    @pytest.mark.asyncio
    async def test_describe_load_balancers_success(self, mock_elbv2_client):
        """Test successful load balancer description."""
        mock_paginator = MagicMock()
        mock_elbv2_client.get_paginator.return_value = mock_paginator
        mock_paginator.paginate.return_value = [
            {
                'LoadBalancers': [
                    {
                        'LoadBalancerArn': 'arn:aws:elasticloadbalancing:us-east-1:123456789:loadbalancer/app/my-alb/123',
                        'LoadBalancerName': 'my-alb',
                        'Type': 'application',
                        'Scheme': 'internet-facing',
                        'State': {'Code': 'active'},
                        'VpcId': 'vpc-123',
                        'AvailabilityZones': [
                            {'ZoneName': 'us-east-1a', 'SubnetId': 'subnet-1'},
                            {'ZoneName': 'us-east-1b', 'SubnetId': 'subnet-2'},
                        ],
                        'CreatedTime': datetime(2025, 1, 1, tzinfo=timezone.utc),
                        'IpAddressType': 'ipv4',
                    }
                ]
            }
        ]
        
        # Mock target groups
        mock_elbv2_client.describe_target_groups.return_value = {
            'TargetGroups': [
                {
                    'TargetGroupArn': 'arn:aws:elasticloadbalancing:us-east-1:123456789:targetgroup/my-tg/123',
                    'TargetGroupName': 'my-tg',
                    'Protocol': 'HTTP',
                    'Port': 80,
                    'TargetType': 'instance',
                    'HealthCheckEnabled': True,
                }
            ]
        }
        
        # Mock target health
        mock_elbv2_client.describe_target_health.return_value = {
            'TargetHealthDescriptions': [
                {
                    'Target': {'Id': 'i-123', 'Port': 80},
                    'TargetHealth': {'State': 'healthy'},
                },
                {
                    'Target': {'Id': 'i-456', 'Port': 80},
                    'TargetHealth': {'State': 'unhealthy', 'Description': 'Health check failed'},
                }
            ]
        }

        ctx = MagicMock()
        result = await describe_load_balancers(ctx, client_id='test-client')

        assert 'load_balancers' in result
        assert result['count'] == 1
        lb = result['load_balancers'][0]
        assert lb['LoadBalancerName'] == 'my-alb'
        assert lb['Type'] == 'application'
        assert len(lb['TargetGroups']) == 1
        assert lb['TargetGroups'][0]['HealthyTargetCount'] == 1
        assert lb['TargetGroups'][0]['UnhealthyTargetCount'] == 1


class TestDescribeNATGateways:
    """Test NAT Gateway inventory functionality."""

    @pytest.fixture
    def mock_ec2_client(self):
        """Mock EC2 client for NAT Gateways."""
        with patch(
            'awslabs.cost_explorer_mcp_server.inventory.network.get_ec2_client'
        ) as mock:
            client = MagicMock()
            mock.return_value = client
            yield client

    @pytest.mark.asyncio
    async def test_describe_nat_gateways_success(self, mock_ec2_client):
        """Test successful NAT Gateway description."""
        mock_paginator = MagicMock()
        mock_ec2_client.get_paginator.return_value = mock_paginator
        mock_paginator.paginate.return_value = [
            {
                'NatGateways': [
                    {
                        'NatGatewayId': 'nat-123',
                        'State': 'available',
                        'VpcId': 'vpc-123',
                        'SubnetId': 'subnet-123',
                        'ConnectivityType': 'public',
                        'CreateTime': datetime(2025, 1, 1, tzinfo=timezone.utc),
                        'NatGatewayAddresses': [
                            {
                                'AllocationId': 'eipalloc-123',
                                'PublicIp': '52.1.2.3',
                                'PrivateIp': '10.0.1.5',
                                'NetworkInterfaceId': 'eni-123',
                            }
                        ],
                        'Tags': [{'Key': 'Name', 'Value': 'prod-nat'}],
                    }
                ]
            }
        ]

        ctx = MagicMock()
        result = await describe_nat_gateways(ctx, client_id='test-client')

        assert 'nat_gateways' in result
        assert result['count'] == 1
        assert result['active_count'] == 1
        # $0.045/hour * 24 hours * 30 days = $32.40
        assert result['estimated_monthly_base_cost_usd'] == 32.4


class TestDescribeElasticIPs:
    """Test Elastic IP inventory functionality."""

    @pytest.fixture
    def mock_ec2_client(self):
        """Mock EC2 client for Elastic IPs."""
        with patch(
            'awslabs.cost_explorer_mcp_server.inventory.network.get_ec2_client'
        ) as mock:
            client = MagicMock()
            mock.return_value = client
            yield client

    @pytest.mark.asyncio
    async def test_describe_elastic_ips_success(self, mock_ec2_client):
        """Test successful Elastic IP description."""
        mock_ec2_client.describe_addresses.return_value = {
            'Addresses': [
                {
                    'AllocationId': 'eipalloc-123',
                    'PublicIp': '52.1.2.3',
                    'AssociationId': 'eipassoc-123',
                    'InstanceId': 'i-123',
                    'Domain': 'vpc',
                    'Tags': [{'Key': 'Name', 'Value': 'web-server'}],
                },
                {
                    'AllocationId': 'eipalloc-456',
                    'PublicIp': '52.4.5.6',
                    'Domain': 'vpc',
                    'Tags': [],
                }
            ]
        }

        ctx = MagicMock()
        result = await describe_elastic_ips(ctx, client_id='test-client')

        assert 'elastic_ips' in result
        assert result['count'] == 2
        assert result['unassociated_count'] == 1
        # $0.005/hour * 24 * 30 = $3.60
        assert result['estimated_monthly_waste_usd'] == 3.6


class TestListS3Buckets:
    """Test S3 bucket inventory functionality."""

    @pytest.fixture
    def mock_s3_client(self):
        """Mock S3 client."""
        with patch(
            'awslabs.cost_explorer_mcp_server.inventory.s3.get_s3_client'
        ) as mock:
            client = MagicMock()
            mock.return_value = client
            # Add exceptions attribute
            client.exceptions = MagicMock()
            client.exceptions.ClientError = Exception
            yield client

    @pytest.mark.asyncio
    async def test_list_s3_buckets_success(self, mock_s3_client):
        """Test successful S3 bucket listing."""
        mock_s3_client.list_buckets.return_value = {
            'Buckets': [
                {
                    'Name': 'my-bucket',
                    'CreationDate': datetime(2025, 1, 1, tzinfo=timezone.utc),
                }
            ]
        }
        
        mock_s3_client.get_bucket_location.return_value = {
            'LocationConstraint': 'us-west-2'
        }
        
        mock_s3_client.get_bucket_lifecycle_configuration.return_value = {
            'Rules': [
                {
                    'ID': 'delete-old',
                    'Status': 'Enabled',
                    'Filter': {'Prefix': ''},
                    'Expiration': {'Days': 365},
                }
            ]
        }
        
        mock_s3_client.get_bucket_versioning.return_value = {
            'Status': 'Enabled'
        }
        
        mock_s3_client.get_bucket_encryption.return_value = {
            'ServerSideEncryptionConfiguration': {
                'Rules': [
                    {
                        'ApplyServerSideEncryptionByDefault': {
                            'SSEAlgorithm': 'AES256'
                        }
                    }
                ]
            }
        }

        ctx = MagicMock()
        result = await list_s3_buckets(ctx, client_id='test-client')

        assert 'buckets' in result
        assert result['count'] == 1
        bucket = result['buckets'][0]
        assert bucket['Name'] == 'my-bucket'
        assert bucket['Region'] == 'us-west-2'
        assert bucket['HasLifecycleRules'] is True
        assert bucket['VersioningStatus'] == 'Enabled'
        assert bucket['EncryptionEnabled'] is True

    @pytest.mark.asyncio
    async def test_list_s3_buckets_no_lifecycle(self, mock_s3_client):
        """Test S3 bucket without lifecycle rules."""
        mock_s3_client.list_buckets.return_value = {
            'Buckets': [
                {
                    'Name': 'no-lifecycle-bucket',
                    'CreationDate': datetime(2025, 1, 1, tzinfo=timezone.utc),
                }
            ]
        }
        
        mock_s3_client.get_bucket_location.return_value = {'LocationConstraint': None}
        
        # Simulate NoSuchLifecycleConfiguration error
        error = Exception('NoSuchLifecycleConfiguration')
        mock_s3_client.get_bucket_lifecycle_configuration.side_effect = error
        
        mock_s3_client.get_bucket_versioning.return_value = {}
        mock_s3_client.get_bucket_encryption.side_effect = Exception('ServerSideEncryptionConfigurationNotFoundError')

        ctx = MagicMock()
        result = await list_s3_buckets(ctx, client_id='test-client')

        assert result['buckets_without_lifecycle'] == 1
