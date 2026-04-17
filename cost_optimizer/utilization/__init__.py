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

"""Utilization handlers for AWS CloudWatch metrics."""

from .ec2 import get_ec2_utilization
from .rds import get_rds_utilization
from .elb import get_elb_utilization
from .ebs import get_ebs_utilization
from .network import get_nat_gateway_utilization
from .multi import get_multi_resource_utilization

__all__ = [
    'get_ec2_utilization',
    'get_rds_utilization',
    'get_elb_utilization',
    'get_ebs_utilization',
    'get_nat_gateway_utilization',
    'get_multi_resource_utilization',
]
