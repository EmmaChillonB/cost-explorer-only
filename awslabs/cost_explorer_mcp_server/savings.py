"""Savings commitments analysis handler.

Provides a single tool that returns everything needed to build a savings strategy:
- Existing Savings Plans (active/expired)
- Existing Reserved Instances (active/expired)
- Coverage and utilization metrics for the last 30 days
- Eligible on-demand spend breakdown
"""

import os
import sys
from datetime import datetime, timezone
from dateutil.relativedelta import relativedelta
from loguru import logger
from mcp.server.fastmcp import Context
from pydantic import Field
from typing import Any, Dict

from .auth import get_cost_explorer_client

# Configure Loguru logging
logger.remove()
logger.add(sys.stderr, level=os.getenv('FASTMCP_LOG_LEVEL', 'WARNING'))


async def get_savings_commitments(
    ctx: Context,
    client_id: str = Field(
        ...,
        description="Client identifier to use for this request. Must exist in clients.json."
    ),
    days_back: int = Field(
        30,
        description="Number of days to look back for coverage/utilization. Default 30."
    ),
) -> Dict[str, Any]:
    """Get complete savings commitment data for strategy planning.

    Returns in a single call:
    1. Active Savings Plans with their utilization
    2. Active Reserved Instances
    3. Savings Plans coverage (% of eligible spend covered) for last N days
    4. Savings Plans utilization (% of commitment used) for last N days
    5. Reserved Instance coverage for last N days
    6. Reserved Instance utilization for last N days
    7. On-demand eligible spend by service (to size new commitments)

    This gives the savings agent everything needed to recommend new commitments
    or flag over/under-provisioned ones.

    Args:
        ctx: MCP context
        client_id: Client identifier for session management
        days_back: Days to look back for metrics (default 30)

    Returns:
        Dictionary with existing commitments, coverage, utilization, and eligible spend
    """
    try:
        now = datetime.now(timezone.utc)
        start_date = (now - relativedelta(days=days_back)).strftime('%Y-%m-%d')
        end_date = now.strftime('%Y-%m-%d')

        ce = get_cost_explorer_client(client_id)

        result: Dict[str, Any] = {
            'period': f'{start_date} to {end_date}',
            'savings_plans': {},
            'reserved_instances': {},
        }

        # --- 1. Savings Plans Coverage ---
        try:
            sp_coverage_resp = ce.get_savings_plans_coverage(
                TimePeriod={'Start': start_date, 'End': end_date},
                Granularity='MONTHLY',
            )
            coverage_entries = []
            for item in sp_coverage_resp.get('SavingsPlansCoverages', []):
                period = item.get('TimePeriod', {})
                cov = item.get('Coverage', {})
                coverage_entries.append({
                    'month': period.get('Start', '')[:7],
                    'coverage_pct': _safe_float(cov.get('CoveragePercentage')),
                    'spend_covered': _safe_float(cov.get('SpendCoveredBySavingsPlans')),
                    'on_demand_cost': _safe_float(cov.get('OnDemandCost')),
                    'total_cost': _safe_float(cov.get('TotalCost')),
                })
            result['savings_plans']['coverage'] = coverage_entries
        except Exception as e:
            logger.error(f'Error getting SP coverage: {e}')
            result['savings_plans']['coverage_error'] = str(e)

        # --- 2. Savings Plans Utilization ---
        try:
            sp_util_resp = ce.get_savings_plans_utilization(
                TimePeriod={'Start': start_date, 'End': end_date},
                Granularity='MONTHLY',
            )
            util_entries = []
            for item in sp_util_resp.get('SavingsPlansUtilizationsByTime', []):
                period = item.get('TimePeriod', {})
                util = item.get('Utilization', {})
                util_entries.append({
                    'month': period.get('Start', '')[:7],
                    'utilization_pct': _safe_float(util.get('UtilizationPercentage')),
                    'total_commitment': _safe_float(util.get('TotalCommitment')),
                    'used_commitment': _safe_float(util.get('UsedCommitment')),
                    'unused_commitment': _safe_float(util.get('UnusedCommitment')),
                })

            # Also get the total/aggregate
            total_util = sp_util_resp.get('Total', {}).get('Utilization', {})
            result['savings_plans']['utilization'] = util_entries
            result['savings_plans']['utilization_total'] = {
                'utilization_pct': _safe_float(total_util.get('UtilizationPercentage')),
                'total_commitment': _safe_float(total_util.get('TotalCommitment')),
                'used_commitment': _safe_float(total_util.get('UsedCommitment')),
                'unused_commitment': _safe_float(total_util.get('UnusedCommitment')),
            }
        except Exception as e:
            logger.error(f'Error getting SP utilization: {e}')
            result['savings_plans']['utilization_error'] = str(e)

        # --- 3. Reserved Instance Coverage ---
        try:
            ri_coverage_resp = ce.get_reservation_coverage(
                TimePeriod={'Start': start_date, 'End': end_date},
                Granularity='MONTHLY',
            )
            ri_cov_entries = []
            for item in ri_coverage_resp.get('CoveragesByTime', []):
                period = item.get('TimePeriod', {})
                total = item.get('Total', {})
                cov_hours = total.get('CoverageHours', {})
                ri_cov_entries.append({
                    'month': period.get('Start', '')[:7],
                    'coverage_pct': _safe_float(cov_hours.get('CoverageHoursPercentage')),
                    'reserved_hours': _safe_float(cov_hours.get('ReservedHours')),
                    'on_demand_hours': _safe_float(cov_hours.get('OnDemandHours')),
                    'total_running_hours': _safe_float(cov_hours.get('TotalRunningHours')),
                })
            result['reserved_instances']['coverage'] = ri_cov_entries
        except Exception as e:
            logger.error(f'Error getting RI coverage: {e}')
            result['reserved_instances']['coverage_error'] = str(e)

        # --- 4. Reserved Instance Utilization ---
        try:
            ri_util_resp = ce.get_reservation_utilization(
                TimePeriod={'Start': start_date, 'End': end_date},
                Granularity='MONTHLY',
            )
            ri_util_entries = []
            for item in ri_util_resp.get('UtilizationsByTime', []):
                period = item.get('TimePeriod', {})
                total = item.get('Total', {})
                ri_util_entries.append({
                    'month': period.get('Start', '')[:7],
                    'utilization_pct': _safe_float(
                        total.get('UtilizationPercentage')
                    ),
                    'purchased_hours': _safe_float(total.get('PurchasedHours')),
                    'total_actual_hours': _safe_float(total.get('TotalActualHours')),
                    'unused_hours': _safe_float(total.get('UnusedHours')),
                    'net_savings': _safe_float(total.get('NetRISavings')),
                })

            total_ri = ri_util_resp.get('Total', {})
            result['reserved_instances']['utilization'] = ri_util_entries
            result['reserved_instances']['utilization_total'] = {
                'utilization_pct': _safe_float(total_ri.get('UtilizationPercentage')),
                'purchased_hours': _safe_float(total_ri.get('PurchasedHours')),
                'total_actual_hours': _safe_float(total_ri.get('TotalActualHours')),
                'unused_hours': _safe_float(total_ri.get('UnusedHours')),
                'net_savings': _safe_float(total_ri.get('NetRISavings')),
            }
        except Exception as e:
            logger.error(f'Error getting RI utilization: {e}')
            result['reserved_instances']['utilization_error'] = str(e)

        # --- 5. On-demand eligible spend by service (for sizing new commitments) ---
        try:
            od_resp = ce.get_cost_and_usage(
                TimePeriod={'Start': start_date, 'End': end_date},
                Granularity='MONTHLY',
                GroupBy=[{'Type': 'DIMENSION', 'Key': 'SERVICE'}],
                Filter={
                    'Dimensions': {
                        'Key': 'PURCHASE_TYPE',
                        'Values': ['On Demand Instances'],
                        'MatchOptions': ['EQUALS'],
                    }
                },
                Metrics=['UnblendedCost'],
            )

            on_demand_by_service: Dict[str, float] = {}
            for rbt in od_resp.get('ResultsByTime', []):
                for group in rbt.get('Groups', []):
                    svc = group['Keys'][0]
                    cost = float(group['Metrics']['UnblendedCost']['Amount'])
                    on_demand_by_service[svc] = (
                        on_demand_by_service.get(svc, 0) + cost
                    )

            # Sort by cost descending and round
            sorted_od = sorted(
                on_demand_by_service.items(), key=lambda x: x[1], reverse=True
            )
            result['on_demand_eligible_spend'] = [
                {'service': svc, 'cost': round(cost, 2)}
                for svc, cost in sorted_od
                if cost > 1
            ]
            result['total_on_demand_spend'] = round(
                sum(on_demand_by_service.values()), 2
            )
        except Exception as e:
            logger.error(f'Error getting on-demand spend: {e}')
            result['on_demand_error'] = str(e)

        # --- 6. Existing SP details (active) ---
        try:
            sp_cost_resp = ce.get_cost_and_usage(
                TimePeriod={'Start': start_date, 'End': end_date},
                Granularity='MONTHLY',
                GroupBy=[{'Type': 'DIMENSION', 'Key': 'SAVINGS_PLANS_TYPE'}],
                Filter={
                    'Dimensions': {
                        'Key': 'RECORD_TYPE',
                        'Values': ['SavingsPlanCoveredUsage', 'SavingsPlanNegation'],
                        'MatchOptions': ['EQUALS'],
                    }
                },
                Metrics=['UnblendedCost'],
            )

            sp_types: Dict[str, float] = {}
            for rbt in sp_cost_resp.get('ResultsByTime', []):
                for group in rbt.get('Groups', []):
                    sp_type = group['Keys'][0]
                    cost = float(group['Metrics']['UnblendedCost']['Amount'])
                    sp_types[sp_type] = sp_types.get(sp_type, 0) + cost

            result['savings_plans']['active_types'] = [
                {'type': t, 'covered_cost': round(c, 2)}
                for t, c in sorted(sp_types.items(), key=lambda x: x[1], reverse=True)
                if abs(c) > 0.01
            ]
        except Exception as e:
            logger.debug(f'Could not get SP type breakdown: {e}')

        result['has_active_savings_plans'] = bool(
            result['savings_plans'].get('utilization_total', {}).get('total_commitment', 0) > 0
        )
        result['has_active_reserved_instances'] = bool(
            result['reserved_instances'].get('utilization_total', {}).get('purchased_hours', 0) > 0
        )

        return result

    except Exception as e:
        logger.error(f'Error getting savings commitments: {e}')
        return {'error': f'Error getting savings commitments: {str(e)}'}


def _safe_float(value) -> float:
    """Safely convert a value to float, returning 0.0 on failure."""
    if value is None:
        return 0.0
    try:
        return round(float(value), 2)
    except (ValueError, TypeError):
        return 0.0
