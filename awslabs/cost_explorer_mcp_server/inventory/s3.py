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

"""S3 inventory handler — returns pre-analyzed data for cost optimization reports.

Only buckets that need attention are returned in detail (missing lifecycle,
test/dev naming). Well-configured buckets are counted in the summary only.
"""

import re
from typing import Any, Dict, List

from loguru import logger
from mcp.server.fastmcp import Context
from pydantic import Field

from ..aws_clients import get_s3_client
from .common import serialize_datetime

# Patterns that suggest test/dev/temporary buckets
_TEST_PATTERNS = re.compile(
    r'(?:^|[-_.])(?:test|dev|qa|staging|sandbox|tmp|poc|demo)(?:[-_.]|$)', re.IGNORECASE
)


async def list_s3_buckets(
    ctx: Context,
    client_id: str = Field(
        ...,
        description="Client identifier to use for this request."
    ),
) -> Dict[str, Any]:
    """Analyze S3 buckets and return only actionable findings for cost optimization.

    Returns a summary of all buckets plus detail lists for:
    - Buckets without lifecycle rules (missing expiration or transition)
    - Buckets with test/dev/qa naming patterns
    Well-configured buckets are only counted in the summary.
    """
    try:
        s3 = get_s3_client(client_id)

        response = s3.list_buckets()

        total = 0
        without_lifecycle = 0
        without_expiration = 0
        without_transitions = 0
        with_versioning = 0

        buckets_needing_lifecycle: List[Dict] = []
        test_dev_buckets: List[Dict] = []

        for bucket in response.get('Buckets', []):
            total += 1
            bucket_name = bucket.get('Name')

            # Get region
            region = 'unknown'
            try:
                loc = s3.get_bucket_location(Bucket=bucket_name)
                region = loc.get('LocationConstraint') or 'us-east-1'
            except Exception:
                pass

            # Get lifecycle flags
            has_lifecycle = False
            has_expiration = False
            has_transition = False
            try:
                lc_resp = s3.get_bucket_lifecycle_configuration(Bucket=bucket_name)
                rules = lc_resp.get('Rules', [])
                active = [r for r in rules if r.get('Status') == 'Enabled']
                has_lifecycle = len(active) > 0
                has_expiration = any(
                    r.get('Expiration') or r.get('NoncurrentVersionExpiration')
                    for r in active
                )
                has_transition = any(
                    r.get('Transitions') or r.get('NoncurrentVersionTransitions')
                    for r in active
                )
            except s3.exceptions.ClientError as e:
                if 'NoSuchLifecycleConfiguration' not in str(e):
                    logger.debug(f'Lifecycle error for {bucket_name}: {e}')
            except Exception:
                pass

            # Get versioning
            versioning = False
            try:
                v_resp = s3.get_bucket_versioning(Bucket=bucket_name)
                versioning = v_resp.get('Status') == 'Enabled'
            except Exception:
                pass

            if versioning:
                with_versioning += 1
            if not has_lifecycle:
                without_lifecycle += 1
            if not has_expiration:
                without_expiration += 1
            if not has_transition:
                without_transitions += 1

            # Collect actionable buckets
            bucket_entry = serialize_datetime({
                'Name': bucket_name,
                'Region': region,
                'CreationDate': bucket.get('CreationDate'),
                'hasLifecycleRules': has_lifecycle,
                'hasExpirationRules': has_expiration,
                'hasTransitionRules': has_transition,
                'VersioningEnabled': versioning,
            })

            if not has_lifecycle or not has_expiration or not has_transition:
                buckets_needing_lifecycle.append(bucket_entry)

            if _TEST_PATTERNS.search(bucket_name):
                test_dev_buckets.append(bucket_entry)

        return {
            'summary': {
                'total': total,
                'with_versioning': with_versioning,
                'without_lifecycle': without_lifecycle,
                'without_expiration': without_expiration,
                'without_transitions': without_transitions,
            },
            'buckets_needing_lifecycle': buckets_needing_lifecycle,
            'test_dev_buckets': test_dev_buckets,
        }

    except Exception as e:
        logger.error(f'Error listing S3 buckets: {e}')
        return {'error': str(e)}
