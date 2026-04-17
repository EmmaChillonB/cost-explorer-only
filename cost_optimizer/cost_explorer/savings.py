"""Savings commitments analysis handler.

Single tool that returns everything needed for savings strategy:
- Current state: active SPs/RIs with coverage & utilization
- AWS recommendations: SP purchase + RI purchase (1yr, No Upfront, 30d lookback)
- On-demand eligible spend (EC2 + RDS only)
"""

import os
import sys
from datetime import datetime, timezone
from dateutil.relativedelta import relativedelta
from loguru import logger
from mcp.server.fastmcp import Context
from pydantic import Field
from typing import Any, Dict

from ..auth import get_cost_explorer_client, get_account_id, is_payer_account, build_account_filter

# Configure Loguru logging
logger.remove()
logger.add(sys.stderr, level=os.getenv('FASTMCP_LOG_LEVEL', 'WARNING'))

# Services eligible for SP/RI commitments
_ELIGIBLE_SERVICES = {
    'Amazon Elastic Compute Cloud - Compute',
    'Amazon Relational Database Service',
    'Amazon ElastiCache',
    'Amazon Redshift',
    'Amazon OpenSearch Service',
    'Amazon DocumentDB (with MongoDB compatibility)',
    'Amazon Neptune',
    'Amazon SageMaker',
}


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
    account_scope: str = Field(
        "auto",
        description="auto: filters payer accounts to own costs only (default). all: consolidated view of all linked accounts. linked: force single-account filter."
    ),
) -> Dict[str, Any]:
    """Get savings data for strategy planning: current state + AWS recommendations.

    Returns:
    - current_state: coverage/utilization for SP and RI, active plans
    - sp_recommendations: AWS Savings Plans purchase recommendations (COMPUTE_SP + EC2_INSTANCE_SP)
    - ri_recommendations: AWS Reserved Instance purchase recommendations (EC2 + RDS)
    - on_demand_eligible_spend: eligible spend by service (only compute + database)
    """
    try:
        now = datetime.now(timezone.utc)
        start_date = (now - relativedelta(days=days_back)).strftime('%Y-%m-%d')
        end_date = now.strftime('%Y-%m-%d')

        ce = get_cost_explorer_client(client_id)

        result: Dict[str, Any] = {'period': f'{start_date} to {end_date}'}

        # ── Current state ─────────────────────────────────────────────────
        current = {
            'sp_coverage_pct': 0,
            'sp_utilization_pct': 0,
            'ri_coverage_pct': 0,
            'ri_utilization_pct': 0,
            'has_active_savings_plans': False,
            'has_active_reserved_instances': False,
            'active_savings_plans': [],
        }

        # SP coverage
        try:
            sp_cov_kwargs = dict(TimePeriod={'Start': start_date, 'End': end_date})
            acct_filter = build_account_filter(client_id, account_scope)
            if acct_filter:
                sp_cov_kwargs['Filters'] = acct_filter
            resp = ce.get_savings_plans_coverage(**sp_cov_kwargs)
            coverages = resp.get('SavingsPlansCoverages', [])
            if coverages:
                covered = sum(
                    _safe_float(c.get('Coverage', {}).get('SpendCoveredBySavingsPlans'))
                    for c in coverages
                )
                total = sum(
                    _safe_float(c.get('Coverage', {}).get('TotalCost'))
                    for c in coverages
                )
                current['sp_coverage_pct'] = round(covered / total * 100, 1) if total > 0 else 0
        except Exception as e:
            logger.debug(f'SP coverage: {e}')

        # SP utilization
        try:
            sp_util_kwargs = dict(TimePeriod={'Start': start_date, 'End': end_date})
            acct_filter = build_account_filter(client_id, account_scope)
            if acct_filter:
                sp_util_kwargs['Filter'] = acct_filter
            resp = ce.get_savings_plans_utilization(**sp_util_kwargs)
            util = resp.get('Total', {}).get('Utilization', {})
            current['sp_utilization_pct'] = _safe_float(util.get('UtilizationPercentage'))
        except Exception as e:
            logger.debug(f'SP utilization: {e}')

        # RI coverage
        try:
            ri_cov_kwargs = dict(TimePeriod={'Start': start_date, 'End': end_date})
            acct_filter = build_account_filter(client_id, account_scope)
            if acct_filter:
                ri_cov_kwargs['Filter'] = acct_filter
            resp = ce.get_reservation_coverage(**ri_cov_kwargs)
            entries = resp.get('CoveragesByTime', [])
            if entries:
                reserved = sum(
                    _safe_float(c.get('Total', {}).get('CoverageHours', {}).get('ReservedHours'))
                    for c in entries
                )
                running = sum(
                    _safe_float(c.get('Total', {}).get('CoverageHours', {}).get('TotalRunningHours'))
                    for c in entries
                )
                current['ri_coverage_pct'] = round(reserved / running * 100, 1) if running > 0 else 0
        except Exception as e:
            logger.debug(f'RI coverage: {e}')

        # RI utilization
        try:
            ri_util_kwargs = dict(TimePeriod={'Start': start_date, 'End': end_date})
            acct_filter = build_account_filter(client_id, account_scope)
            if acct_filter:
                ri_util_kwargs['Filter'] = acct_filter
            resp = ce.get_reservation_utilization(**ri_util_kwargs)
            total_ri = resp.get('Total', {})
            current['ri_utilization_pct'] = _safe_float(total_ri.get('UtilizationPercentage'))
        except Exception as e:
            logger.debug(f'RI utilization: {e}')

        # Active Savings Plans details
        try:
            from ..aws_clients import get_aws_client
            sp_client = get_aws_client(client_id, 'savingsplans', 'us-east-1')
            sp_resp = sp_client.describe_savings_plans(States=['active', 'queued'])
            for plan in sp_resp.get('savingsPlans', []):
                current['active_savings_plans'].append({
                    'type': plan.get('savingsPlanType'),
                    'payment_option': plan.get('paymentOption'),
                    'state': plan.get('state'),
                    'start': plan.get('start'),
                    'end': plan.get('end'),
                    'commitment_per_hour': plan.get('commitment'),
                })
        except Exception as e:
            logger.debug(f'Active SP details: {e}')

        current['has_active_savings_plans'] = (
            current['sp_coverage_pct'] > 0 or len(current['active_savings_plans']) > 0
        )
        current['has_active_reserved_instances'] = current['ri_coverage_pct'] > 0

        result['current_state'] = current

        # ── On-demand eligible spend (compute + database only) ────────────
        try:
            od_filter = build_account_filter(client_id, account_scope, {
                'Dimensions': {
                    'Key': 'PURCHASE_TYPE',
                    'Values': ['On Demand Instances'],
                    'MatchOptions': ['EQUALS'],
                }
            })
            od_resp = ce.get_cost_and_usage(
                TimePeriod={'Start': start_date, 'End': end_date},
                Granularity='MONTHLY',
                GroupBy=[{'Type': 'DIMENSION', 'Key': 'SERVICE'}],
                Filter=od_filter,
                Metrics=['UnblendedCost'],
            )
            od_by_svc: Dict[str, float] = {}
            for rbt in od_resp.get('ResultsByTime', []):
                for group in rbt.get('Groups', []):
                    svc = group['Keys'][0]
                    cost = float(group['Metrics']['UnblendedCost']['Amount'])
                    od_by_svc[svc] = od_by_svc.get(svc, 0) + cost

            eligible = [
                {'service': svc, 'cost': round(cost, 2)}
                for svc, cost in sorted(od_by_svc.items(), key=lambda x: x[1], reverse=True)
                if cost > 1 and svc in _ELIGIBLE_SERVICES
            ]
            result['on_demand_eligible'] = eligible
            result['total_on_demand_eligible'] = round(sum(i['cost'] for i in eligible), 2)
        except Exception as e:
            logger.debug(f'On-demand spend: {e}')
            result['on_demand_eligible'] = []
            result['total_on_demand_eligible'] = 0

        # ── SP purchase recommendations from AWS ──────────────────────────
        sp_recs = {}
        # Payer accounts need AccountScope=LINKED to get per-account recommendations
        # Linked accounts get their own recommendations naturally
        _needs_scope = (account_scope != "all") and is_payer_account(client_id)
        _scope_extra = {'AccountScope': 'LINKED'} if _needs_scope else {}
        for sp_type in ['COMPUTE_SP', 'EC2_INSTANCE_SP']:
            try:
                resp = ce.get_savings_plans_purchase_recommendation(
                    SavingsPlansType=sp_type,
                    TermInYears='ONE_YEAR',
                    PaymentOption='NO_UPFRONT',
                    LookbackPeriodInDays='THIRTY_DAYS',
                    **_scope_extra,
                )
                rec_data = resp.get('SavingsPlansPurchaseRecommendation', {})
                details = rec_data.get('SavingsPlansPurchaseRecommendationDetails', [])
                summary = rec_data.get('SavingsPlansPurchaseRecommendationSummary', {})

                items = []
                for d in details:
                    sp_info = d.get('SavingsPlansDetails', {})
                    sp_cost = _safe_float(d.get('EstimatedSPCost'))
                    od_remaining = _safe_float(d.get('EstimatedOnDemandCost'))
                    total_after = sp_cost + od_remaining
                    coverage_pct = round(sp_cost / total_after * 100, 1) if total_after > 0 else 0

                    items.append({
                        'hourly_commitment': d.get('HourlyCommitmentToPurchase'),
                        'region': sp_info.get('Region'),
                        'instance_family': sp_info.get('InstanceFamily'),
                        'estimated_monthly_savings': _safe_float(d.get('EstimatedMonthlySavingsAmount')),
                        'estimated_savings_pct': _safe_float(d.get('EstimatedSavingsPercentage')),
                        'estimated_coverage_pct': coverage_pct,
                        'estimated_utilization_pct': _safe_float(d.get('EstimatedAverageUtilization')),
                    })

                if items:
                    key = 'compute_sp' if sp_type == 'COMPUTE_SP' else 'ec2_instance_sp'
                    sp_recs[key] = {
                        'hourly_commitment': summary.get('HourlyCommitmentToPurchase'),
                        'estimated_monthly_savings': _safe_float(summary.get('EstimatedMonthlySavingsAmount')),
                        'estimated_savings_pct': _safe_float(summary.get('EstimatedSavingsPercentage')),
                        'details': items,
                    }
            except Exception as e:
                logger.debug(f'SP recommendation {sp_type}: {e}')

        result['sp_recommendations'] = sp_recs if sp_recs else None

        # ── RI purchase recommendations from AWS ──────────────────────────
        ri_recs = {}
        for service_name in [
            'Amazon Elastic Compute Cloud - Compute',
            'Amazon Relational Database Service',
        ]:
            short = 'ec2' if 'Compute' in service_name else 'rds'
            try:
                ri_kwargs = dict(
                    Service=service_name,
                    TermInYears='ONE_YEAR',
                    PaymentOption='NO_UPFRONT',
                    LookbackPeriodInDays='THIRTY_DAYS',
                )
                if _needs_scope:
                    _acct = get_account_id(client_id)
                    if _acct:
                        ri_kwargs['AccountScope'] = 'LINKED'
                        ri_kwargs['AccountId'] = _acct
                resp = ce.get_reservation_purchase_recommendation(**ri_kwargs)
                items = []
                for rec in resp.get('Recommendations', []):
                    for d in rec.get('RecommendationDetails', []):
                        inst = d.get('InstanceDetails', {})
                        if short == 'ec2':
                            det = inst.get('EC2InstanceDetails', {})
                            platform = det.get('Platform', '')
                        else:
                            det = inst.get('RDSInstanceDetails', {})
                            platform = det.get('DatabaseEngine', '')

                        monthly_savings = _safe_float(d.get('EstimatedMonthlySavingsAmount'))
                        monthly_od = _safe_float(d.get('EstimatedMonthlyOnDemandCost'))
                        rec_count = _safe_float(d.get('RecommendedNumberOfInstancesToPurchase'))
                        max_used = _safe_float(d.get('MaximumNumberOfInstancesUsedPerHour'))
                        avg_util = _safe_float(d.get('AverageUtilization'))
                        coverage_pct = (
                            round(rec_count / max_used * 100, 1)
                            if max_used > 0 else 0
                        )

                        items.append({
                            'instance_type': det.get('InstanceType', ''),
                            'region': det.get('Region', ''),
                            'platform': platform,
                            'recommended_count': d.get('RecommendedNumberOfInstancesToPurchase', '0'),
                            'estimated_monthly_savings': monthly_savings,
                            'recurring_monthly_cost': _safe_float(d.get('RecurringStandardMonthlyCost')),
                            'savings_pct': round(monthly_savings / monthly_od * 100, 1) if monthly_od > 0 else 0,
                            'estimated_coverage_pct': coverage_pct,
                            'estimated_utilization_pct': avg_util,
                        })

                if items:
                    ri_recs[short] = items
            except Exception as e:
                logger.debug(f'RI recommendation {short}: {e}')

        result['ri_recommendations'] = ri_recs if ri_recs else None

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
