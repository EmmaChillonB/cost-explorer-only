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

"""S3 inventory handler — FinOps-oriented analysis with pre-computed intelligence.

Returns structured, token-efficient data with:
- Pre-classified bucket types (cloudtrail, backup, tfstate, etc.)
- Lifecycle status as descriptive string instead of booleans
- Priority hints based on size + name + lifecycle
- Pre-computed top lists (by size, by objects, review candidates)
- Low-priority buckets compacted to reduce noise
"""

import asyncio
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from loguru import logger
from mcp.server.fastmcp import Context
from pydantic import Field

from ..aws_clients import get_s3_client, get_cloudwatch_client
from .common import serialize_datetime

# Max parallel S3 describe calls per tool invocation.
# S3 API has generous limits; 10 concurrent buckets keeps us safe while
# dramatically reducing total time for accounts with many buckets.
_S3_MAX_CONCURRENT = 10

# ── Bucket type inference from name ──────────────────────────

_TYPE_PATTERNS: list[tuple[str, re.Pattern]] = [
    ('cloudtrail', re.compile(r'(?:^|[-_.])(?:cloudtrail|trail|audit)(?:[-_.]|$)', re.I)),
    ('backup', re.compile(r'(?:^|[-_.])(?:backup|bak|archive|snapshot|velero)(?:[-_.]|$)', re.I)),
    ('logs', re.compile(r'(?:^|[-_.])(?:logs?|logging)(?:[-_.]|$)', re.I)),
    ('tfstate', re.compile(r'(?:^|[-_.])(?:tfstate|terraform-state)(?:[-_.]|$)', re.I)),
    ('terraform', re.compile(r'(?:^|[-_.])(?:terraform|tf|pulumi)(?:[-_.]|$)', re.I)),
    ('docs', re.compile(r'(?:^|[-_.])(?:docs?|kb|knowledge)(?:[-_.]|$)', re.I)),
    ('teleport', re.compile(r'(?:^|[-_.])(?:teleport|certbot)(?:[-_.]|$)', re.I)),
    ('config', re.compile(r'(?:^|[-_.])(?:config|aws-config)(?:[-_.]|$)', re.I)),
    ('test_dev', re.compile(r'(?:^|[-_.])(?:test|dev|qa|staging|sandbox|tmp|poc|demo)(?:[-_.]|$)', re.I)),
]

# Size threshold for "low priority" (GB)
_LOW_PRIORITY_SIZE_GB = 0.1


def _infer_bucket_type(name: str) -> str:
    """Infer bucket type from naming patterns."""
    for btype, pattern in _TYPE_PATTERNS:
        if pattern.search(name):
            return btype
    return 'unknown'


def _compute_lifecycle_status(has_lifecycle: bool, has_expiration: bool, has_transition: bool) -> str:
    """Return a descriptive lifecycle status string."""
    if not has_lifecycle:
        return 'none'
    parts = []
    if has_expiration:
        parts.append('expiration')
    if has_transition:
        parts.append('transition')
    if parts:
        return '+'.join(parts)
    return 'rules_only'


def _compute_priority_hint(size_gb: Optional[float], lifecycle_status: str, bucket_type: str) -> str:
    """Compute a priority hint based on size + lifecycle + type."""
    if size_gb is not None and size_gb < _LOW_PRIORITY_SIZE_GB:
        return 'low'
    if lifecycle_status == 'none':
        if size_gb is not None and size_gb > 5.0:
            return 'medium'
        if bucket_type in ('cloudtrail', 'backup', 'logs'):
            return 'medium'
        return 'low'
    return 'low'


def _compute_optimization_signal(lifecycle_status: str, bucket_type: str, size_gb: Optional[float]) -> str:
    """Compute the optimization signal for this bucket."""
    if lifecycle_status != 'none':
        return 'none'
    if bucket_type in ('cloudtrail', 'logs'):
        return 'retention_review'
    if bucket_type == 'backup':
        return 'storage_class_review'
    if bucket_type in ('tfstate', 'terraform', 'config'):
        return 'low_priority_review'
    if bucket_type == 'test_dev':
        return 'cleanup_review'
    if size_gb is not None and size_gb > 5.0:
        return 'lifecycle_review'
    return 'lifecycle_review'


def _get_bucket_metrics(cloudwatch_client, bucket_name: str) -> tuple[Optional[float], Optional[int]]:
    """Get bucket size (GB) and object count from CloudWatch S3 metrics."""
    size_gb = None
    object_count = None
    try:
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=3)
        dims = [
            {'Name': 'BucketName', 'Value': bucket_name},
            {'Name': 'StorageType', 'Value': 'StandardStorage'},
        ]

        # Size
        resp = cloudwatch_client.get_metric_statistics(
            Namespace='AWS/S3', MetricName='BucketSizeBytes',
            Dimensions=dims, StartTime=start, EndTime=end,
            Period=86400, Statistics=['Average'],
        )
        datapoints = resp.get('Datapoints', [])
        if datapoints:
            latest = max(datapoints, key=lambda d: d['Timestamp'])
            size_gb = round(latest.get('Average', 0) / (1024 ** 3), 2)

        # Object count
        dims_all = [
            {'Name': 'BucketName', 'Value': bucket_name},
            {'Name': 'StorageType', 'Value': 'AllStorageTypes'},
        ]
        resp = cloudwatch_client.get_metric_statistics(
            Namespace='AWS/S3', MetricName='NumberOfObjects',
            Dimensions=dims_all, StartTime=start, EndTime=end,
            Period=86400, Statistics=['Average'],
        )
        datapoints = resp.get('Datapoints', [])
        if datapoints:
            latest = max(datapoints, key=lambda d: d['Timestamp'])
            object_count = int(latest.get('Average', 0))
    except Exception:
        pass
    return size_gb, object_count


def _build_bucket_entry(
    name: str,
    region: str,
    size_gb: Optional[float],
    object_count: Optional[int],
    versioning: bool,
    lifecycle_status: str,
    bucket_type: str,
    priority_hint: str,
    optimization_signal: str,
    compact: bool = False,
) -> Dict[str, Any]:
    """Build a bucket entry dict, optionally compact for low-priority."""
    if compact:
        entry: Dict[str, Any] = {
            'name': name,
            'bucket_type_inferred': bucket_type,
            'optimization_signal': optimization_signal,
        }
        if size_gb is not None:
            entry['size_gb'] = size_gb
        return entry

    entry = {
        'name': name,
        'region': region,
        'size_gb': size_gb,
        'object_count': object_count,
        'metrics_available': size_gb is not None,
        'versioning_enabled': versioning,
        'lifecycle_status': lifecycle_status,
        'bucket_type_inferred': bucket_type,
        'priority_hint': priority_hint,
        'optimization_signal': optimization_signal,
    }
    return entry


def _describe_single_bucket(s3, cw, bucket_name: str) -> tuple:
    """Fetch all per-bucket info in a single sync call (meant to run in a thread).

    Returns: (region, has_lifecycle, has_expiration, has_transition,
              versioning, size_gb, object_count)
    """
    # Region
    region = 'unknown'
    try:
        loc = s3.get_bucket_location(Bucket=bucket_name)
        region = loc.get('LocationConstraint') or 'us-east-1'
    except Exception:
        pass

    # Lifecycle
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
    except Exception as e:
        if 'NoSuchLifecycleConfiguration' not in str(e):
            logger.debug(f'Lifecycle error for {bucket_name}: {e}')

    # Versioning
    versioning = False
    try:
        v_resp = s3.get_bucket_versioning(Bucket=bucket_name)
        versioning = v_resp.get('Status') == 'Enabled'
    except Exception:
        pass

    # CloudWatch metrics (size + object count)
    size_gb, object_count = _get_bucket_metrics(cw, bucket_name) if cw else (None, None)

    return (
        region, has_lifecycle, has_expiration, has_transition,
        versioning, size_gb, object_count,
    )


async def list_s3_buckets(
    ctx: Context,
    client_id: str = Field(
        ...,
        description="Client identifier to use for this request."
    ),
) -> Dict[str, Any]:
    """Analyze S3 buckets with FinOps intelligence.

    Returns pre-computed lists with bucket classification, priority hints,
    and optimization signals. Designed to minimize LLM token usage.
    """
    try:
        s3 = get_s3_client(client_id)

        try:
            cw = get_cloudwatch_client(client_id)
        except Exception:
            cw = None

        response = s3.list_buckets()

        # Collect all bucket data first
        all_buckets: List[Dict[str, Any]] = []
        without_lifecycle = 0
        with_versioning = 0
        total_size_gb = 0.0
        large_without_lifecycle = 0
        small_without_lifecycle = 0

        # Process buckets in parallel. Each bucket needs ~5 AWS calls
        # (location, lifecycle, versioning, size, object count). Doing this
        # sequentially is O(N*5) round-trips; with 40+ buckets it exceeds the
        # MCP SSE read timeout. Parallelizing cuts total time ~10x.
        buckets_list = response.get('Buckets', [])
        total = len(buckets_list)

        sem = asyncio.Semaphore(_S3_MAX_CONCURRENT)

        async def _process_bucket(bucket):
            bucket_name = bucket.get('Name')
            async with sem:
                # Run the sync boto3 calls in a worker thread so they can
                # truly run in parallel across buckets.
                return await asyncio.to_thread(
                    _describe_single_bucket, s3, cw, bucket_name
                )

        results = await asyncio.gather(
            *[_process_bucket(b) for b in buckets_list],
            return_exceptions=True,
        )

        for bucket_name, result in zip((b.get('Name') for b in buckets_list), results):
            if isinstance(result, Exception):
                logger.debug(f'Error processing bucket {bucket_name}: {result}')
                continue

            (region, has_lifecycle, has_expiration, has_transition,
             versioning, size_gb, object_count) = result

            # Computed fields
            lifecycle_status = _compute_lifecycle_status(has_lifecycle, has_expiration, has_transition)
            bucket_type = _infer_bucket_type(bucket_name)
            priority_hint = _compute_priority_hint(size_gb, lifecycle_status, bucket_type)
            optimization_signal = _compute_optimization_signal(lifecycle_status, bucket_type, size_gb)

            # Summary counters
            if versioning:
                with_versioning += 1
            if not has_lifecycle:
                without_lifecycle += 1
                if size_gb is not None and size_gb > 1.0:
                    large_without_lifecycle += 1
                else:
                    small_without_lifecycle += 1
            if size_gb:
                total_size_gb += size_gb

            all_buckets.append({
                'name': bucket_name,
                'region': region,
                'size_gb': size_gb,
                'object_count': object_count,
                'versioning': versioning,
                'lifecycle_status': lifecycle_status,
                'bucket_type': bucket_type,
                'priority_hint': priority_hint,
                'optimization_signal': optimization_signal,
            })

        # ── Build pre-computed top lists ──────────────────────

        # Top by size (exclude nulls, top 5)
        with_size = [b for b in all_buckets if b['size_gb'] is not None]
        by_size = sorted(with_size, key=lambda b: b['size_gb'], reverse=True)[:5]

        # Top by object count (exclude nulls, top 5)
        with_objects = [b for b in all_buckets if b['object_count'] is not None and b['object_count'] > 0]
        by_objects = sorted(with_objects, key=lambda b: b['object_count'], reverse=True)[:5]

        # Review candidates: without lifecycle + medium/high priority
        review = [
            b for b in all_buckets
            if b['lifecycle_status'] == 'none' and b['priority_hint'] != 'low'
        ]
        review.sort(key=lambda b: b.get('size_gb') or 0, reverse=True)

        # Low priority: small or already configured
        low_prio = [
            b for b in all_buckets
            if b['priority_hint'] == 'low' and b['lifecycle_status'] == 'none'
        ]

        # Deduplicate: track which buckets are already in review_candidates
        review_names = {b['name'] for b in review}

        def _to_entry(b: Dict, compact: bool = False) -> Dict:
            return _build_bucket_entry(
                name=b['name'], region=b['region'],
                size_gb=b['size_gb'], object_count=b['object_count'],
                versioning=b['versioning'],
                lifecycle_status=b['lifecycle_status'],
                bucket_type=b['bucket_type'],
                priority_hint=b['priority_hint'],
                optimization_signal=b['optimization_signal'],
                compact=compact,
            )

        return {
            'summary': {
                'total_buckets': total,
                'total_size_gb': round(total_size_gb, 2),
                'buckets_without_lifecycle': without_lifecycle,
                'buckets_with_versioning': with_versioning,
                'large_buckets_without_lifecycle': large_without_lifecycle,
                'small_buckets_without_lifecycle': small_without_lifecycle,
            },
            'top_by_size': [_to_entry(b) for b in by_size],
            # Return the real top 5 by object count even if some of them
            # also appear in top_by_size. A bucket can legitimately be "top
            # by size" and "top by object count" at the same time, and it's
            # useful info for the consumer. The previous behaviour deduped
            # the two lists which left top_by_object_count almost empty on
            # accounts where the biggest buckets also have the most objects
            # (e.g. CloudTrail buckets).
            'top_by_object_count': [_to_entry(b) for b in by_objects],
            'review_candidates': [_to_entry(b) for b in review],
            'low_priority_buckets': [_to_entry(b, compact=True) for b in low_prio],
        }

    except Exception as e:
        logger.error(f'Error listing S3 buckets: {e}')
        return {'error': str(e)}
