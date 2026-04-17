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

"""Inventory handlers for AWS resources."""

from .ec2 import describe_ec2_instances, list_ec2_regions_with_instances
from .rds import describe_rds_instances
from .ebs import describe_ebs_volumes, describe_ebs_snapshots
from .elb import describe_load_balancers
from .network import describe_nat_gateways, describe_elastic_ips
from .s3 import list_s3_buckets

__all__ = [
    'describe_ec2_instances',
    'list_ec2_regions_with_instances',
    'describe_rds_instances',
    'describe_ebs_volumes',
    'describe_ebs_snapshots',
    'describe_load_balancers',
    'describe_nat_gateways',
    'describe_elastic_ips',
    'list_s3_buckets',
]
