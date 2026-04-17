"""Cost trend analysis with automatic anomaly detection and drill-down."""

import os
import sys
from datetime import datetime, timezone
from dateutil.relativedelta import relativedelta
from loguru import logger
from mcp.server.fastmcp import Context
from pydantic import Field
from typing import Any, Dict, Optional

from ..auth import get_cost_explorer_client, build_account_filter

# Configure Loguru logging
logger.remove()
logger.add(sys.stderr, level=os.getenv('FASTMCP_LOG_LEVEL', 'WARNING'))

# Threshold for anomaly detection
ANOMALY_GROWTH_THRESHOLD = 10.0  # percent
# Services to exclude from anomaly drill-down
EXCLUDED_SERVICES = {'Tax', 'AWS Support'}


async def get_cost_trend_with_anomalies(
    ctx: Context,
    client_id: str = Field(
        ...,
        description="Client identifier to use for this request. Must exist in clients.json."
    ),
    months_back: int = Field(
        6,
        description="Number of months to look back for trend analysis. Default 6."
    ),
    anomaly_threshold_pct: float = Field(
        10.0,
        description="Minimum month-over-month growth percentage to flag as anomaly. Default 10."
    ),
    account_scope: str = Field(
        "auto",
        description="auto: filters payer accounts to own costs only (default). all: consolidated view of all linked accounts. linked: force single-account filter."
    ),
) -> Dict[str, Any]:
    """Analyze cost trends over N months and auto-detect anomalies with drill-down.

    This tool performs a complete historical cost analysis in a single call:
    1. Gets today's date (no extra call needed)
    2. Retrieves monthly costs by SERVICE for the last N months
    3. Detects services with month-over-month growth above the threshold
    4. For ALL anomalies with significant $ impact, drills down by USAGE_TYPE
       to identify what changed — returning only the previous and current values

    Returns a compact result ready for hypothesis formulation.

    Args:
        ctx: MCP context
        client_id: Client identifier for session management
        months_back: Number of months to analyze (default 6)
        anomaly_threshold_pct: Growth % threshold for anomaly detection (default 10)

    Returns:
        Dictionary with today_date, cost_by_service_month, top_services_last_month,
        anomalies (all with drill-down details)
    """
    try:
        now = datetime.now(timezone.utc)
        today_str = now.strftime('%Y-%m-%d')

        # Calculate date range: first of month N months ago to first of current month
        start_date = (now - relativedelta(months=months_back)).replace(day=1)
        # End date is first of current month (exclusive for AWS API)
        end_date = now.replace(day=1)
        # If we're past day 1, include current partial month
        if now.day > 1:
            end_date = (now + relativedelta(months=1)).replace(day=1)

        start_str = start_date.strftime('%Y-%m-%d')
        end_str = end_date.strftime('%Y-%m-%d')

        ce = get_cost_explorer_client(client_id)

        # --- Step 1: Get monthly costs grouped by SERVICE ---
        ce_kwargs = dict(
            TimePeriod={'Start': start_str, 'End': end_str},
            Granularity='MONTHLY',
            GroupBy=[{'Type': 'DIMENSION', 'Key': 'SERVICE'}],
            Metrics=['UnblendedCost'],
        )
        acct_filter = build_account_filter(client_id, account_scope)
        if acct_filter:
            ce_kwargs['Filter'] = acct_filter
        response = ce.get_cost_and_usage(**ce_kwargs)

        # Parse into {month: {service: cost}}
        monthly_costs: Dict[str, Dict[str, float]] = {}
        for result_by_time in response.get('ResultsByTime', []):
            month = result_by_time['TimePeriod']['Start'][:7]  # YYYY-MM
            monthly_costs[month] = {}
            for group in result_by_time.get('Groups', []):
                service = group['Keys'][0]
                cost = round(float(group['Metrics']['UnblendedCost']['Amount']), 2)
                if cost > 0:
                    monthly_costs[month][service] = cost

        sorted_months = sorted(monthly_costs.keys())

        # --- Build top services for last complete month ---
        last_month = sorted_months[-1] if sorted_months else None
        top_services_last_month = []
        if last_month:
            total_last = sum(monthly_costs[last_month].values())
            sorted_services = sorted(
                monthly_costs[last_month].items(), key=lambda x: x[1], reverse=True
            )
            for svc, cost in sorted_services[:10]:
                if svc in EXCLUDED_SERVICES:
                    continue
                top_services_last_month.append({
                    'service': svc,
                    'cost': cost,
                    'percentage': round(cost / total_last * 100, 1) if total_last > 0 else 0,
                })

        # --- Step 2: Detect anomalies (month-over-month growth > threshold) ---
        raw_anomalies = []
        for i in range(1, len(sorted_months)):
            prev_month = sorted_months[i - 1]
            curr_month = sorted_months[i]
            all_services = set(monthly_costs[prev_month].keys()) | set(
                monthly_costs[curr_month].keys()
            )

            for service in all_services:
                if service in EXCLUDED_SERVICES:
                    continue
                prev_cost = monthly_costs[prev_month].get(service, 0)
                curr_cost = monthly_costs[curr_month].get(service, 0)

                if prev_cost <= 0:
                    # New service appeared
                    if curr_cost > 20:
                        raw_anomalies.append({
                            'service': service,
                            'period': f'{prev_month} vs {curr_month}',
                            'prev_month': prev_month,
                            'curr_month': curr_month,
                            'prev_cost': 0,
                            'curr_cost': curr_cost,
                            'growth_pct': None,
                            'abs_diff': curr_cost,
                            'is_new': True,
                        })
                    continue

                growth_pct = ((curr_cost - prev_cost) / prev_cost) * 100
                abs_diff = curr_cost - prev_cost

                if growth_pct > anomaly_threshold_pct and abs_diff > 5:
                    raw_anomalies.append({
                        'service': service,
                        'period': f'{prev_month} vs {curr_month}',
                        'prev_month': prev_month,
                        'curr_month': curr_month,
                        'prev_cost': prev_cost,
                        'curr_cost': curr_cost,
                        'growth_pct': round(growth_pct, 1),
                        'abs_diff': round(abs_diff, 2),
                        'is_new': False,
                    })

        # Deduplicate: keep only the highest-impact anomaly per service
        best_per_service: Dict[str, Dict] = {}
        for a in raw_anomalies:
            svc = a['service']
            if svc not in best_per_service or a['abs_diff'] > best_per_service[svc]['abs_diff']:
                best_per_service[svc] = a
        sorted_anomalies = sorted(best_per_service.values(), key=lambda x: x['abs_diff'], reverse=True)

        # --- Step 3: Drill-down ALL anomalies by USAGE_TYPE ---
        # Use proportional threshold: skip drill-down for anomalies below 2% of
        # last month total cost (min $5) — this ensures small accounts get drill-downs too
        last_month_total = sum(monthly_costs.get(sorted_months[-1], {}).values()) if sorted_months else 0
        drill_down_threshold = max(5, last_month_total * 0.02)

        anomalies_output = []
        drill_down_count = 0

        for anomaly in sorted_anomalies:
            base_entry = {
                'service': anomaly['service'],
                'period': anomaly['period'],
                'prev_cost': anomaly['prev_cost'],
                'curr_cost': anomaly['curr_cost'],
                'growth_pct': anomaly['growth_pct'],
                'abs_diff': anomaly['abs_diff'],
            }

            # Skip drill-down for small anomalies (proportional to account size)
            if anomaly['abs_diff'] < drill_down_threshold:
                base_entry['top_drivers'] = []
                base_entry['new_components'] = []
                anomalies_output.append(base_entry)
                continue

            service = anomaly['service']
            curr_m = anomaly['curr_month']
            prev_m = anomaly['prev_month']

            curr_start = f'{curr_m}-01'
            curr_end_dt = datetime.strptime(curr_start, '%Y-%m-%d') + relativedelta(months=1)
            curr_end = curr_end_dt.strftime('%Y-%m-%d')

            prev_start = f'{prev_m}-01'
            prev_end_dt = datetime.strptime(prev_start, '%Y-%m-%d') + relativedelta(months=1)
            prev_end = prev_end_dt.strftime('%Y-%m-%d')

            try:
                # Current month by usage type
                svc_filter = build_account_filter(client_id, account_scope, {
                    'Dimensions': {
                        'Key': 'SERVICE',
                        'Values': [service],
                        'MatchOptions': ['EQUALS'],
                    }
                })
                curr_resp = ce.get_cost_and_usage(
                    TimePeriod={'Start': curr_start, 'End': curr_end},
                    Granularity='MONTHLY',
                    GroupBy=[{'Type': 'DIMENSION', 'Key': 'USAGE_TYPE'}],
                    Filter=svc_filter,
                    Metrics=['UnblendedCost'],
                )

                # Previous month by usage type
                prev_resp = ce.get_cost_and_usage(
                    TimePeriod={'Start': prev_start, 'End': prev_end},
                    Granularity='MONTHLY',
                    GroupBy=[{'Type': 'DIMENSION', 'Key': 'USAGE_TYPE'}],
                    Filter=svc_filter,
                    Metrics=['UnblendedCost'],
                )

                drill_down_count += 1

                # Parse usage types
                curr_usage = {}
                for rbt in curr_resp.get('ResultsByTime', []):
                    for g in rbt.get('Groups', []):
                        ut = g['Keys'][0]
                        cost = round(float(g['Metrics']['UnblendedCost']['Amount']), 2)
                        if cost > 0:
                            curr_usage[ut] = cost

                prev_usage = {}
                for rbt in prev_resp.get('ResultsByTime', []):
                    for g in rbt.get('Groups', []):
                        ut = g['Keys'][0]
                        cost = round(float(g['Metrics']['UnblendedCost']['Amount']), 2)
                        if cost > 0:
                            prev_usage[ut] = cost

                # Find drivers: usage types that increased the most
                all_usage_types = set(curr_usage.keys()) | set(prev_usage.keys())
                drivers = []
                new_components = []
                for ut in all_usage_types:
                    p = prev_usage.get(ut, 0)
                    c = curr_usage.get(ut, 0)
                    diff = c - p
                    if diff > 1:  # Only meaningful changes
                        entry = {
                            'usage_type': ut,
                            'previous': p,
                            'current': c,
                            'diff': round(diff, 2),
                        }
                        if p == 0:
                            new_components.append(entry)
                        else:
                            drivers.append(entry)

                drivers.sort(key=lambda x: x['diff'], reverse=True)
                new_components.sort(key=lambda x: x['diff'], reverse=True)

                base_entry['top_drivers'] = drivers[:5]
                base_entry['new_components'] = new_components[:5]
                anomalies_output.append(base_entry)

            except Exception as e:
                logger.error(f'Error drilling down {service}: {e}')
                base_entry['top_drivers'] = []
                base_entry['new_components'] = []
                base_entry['drill_down_error'] = str(e)
                anomalies_output.append(base_entry)

        # --- Build compact monthly trend ---
        cost_monthly_trend = []
        for month in sorted_months:
            for service, cost in sorted(
                monthly_costs[month].items(), key=lambda x: x[1], reverse=True
            ):
                if service not in EXCLUDED_SERVICES:
                    cost_monthly_trend.append({
                        'month': month,
                        'service': service,
                        'cost': cost,
                    })

        return {
            'today_date': today_str,
            'period': f'{sorted_months[0]} to {sorted_months[-1]}' if sorted_months else '',
            'cost_monthly_trend': cost_monthly_trend,
            'top_services_last_month': top_services_last_month,
            'anomalies': anomalies_output,
            'api_calls_made': 1 + drill_down_count * 2,
        }

    except Exception as e:
        logger.error(f'Error in cost trend analysis: {e}')
        return {'error': f'Error in cost trend analysis: {str(e)}'}
