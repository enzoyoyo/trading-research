#!/usr/bin/env python3
"""A-share post-close data-contract review. No network or execution authority."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from collections import Counter, defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


def finite(value):
    try:
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    except (OverflowError, ValueError):
        return False


def review_flows(snapshot: Any, trade_date: Any):
    gaps, normalized = [], []
    result = {'complete': False, 'rows': [], 'scope_total_1d_yi': None,
              'classification_basis': 'window_signs_not_daily_streaks'}
    if not isinstance(snapshot, dict):
        return result, ['flow_snapshot_missing']
    keys = ('source', 'source_ref', 'taxonomy', 'universe_id')
    for key in keys:
        if not isinstance(snapshot.get(key), str) or not snapshot[key].strip():
            gaps.append(f'flow_{key}_missing')
    if snapshot.get('data_date') != trade_date:
        gaps.append('flow_date_mismatch')
    factor = {'CNY': 1e-8, 'CNY_10K': 1e-4, 'CNY_100M': 1.0}.get(str(snapshot.get('unit')))
    if factor is None:
        gaps.append('flow_unit_unknown')
    expected, rows = snapshot.get('expected_sector_ids'), snapshot.get('rows')
    if not isinstance(expected, list) or not expected or any(not isinstance(x, str) or not x for x in expected):
        gaps.append('flow_expected_universe_missing')
        expected = []
    if len(set(expected)) != len(expected):
        gaps.append('flow_expected_universe_duplicate')
    if not isinstance(rows, list) or not rows:
        return result, gaps + ['flow_rows_missing']
    identities = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            gaps.append(f'flow_row_invalid:{index}')
            continue
        sector_id = row.get('sector_id')
        if not isinstance(sector_id, str) or not sector_id:
            gaps.append(f'flow_sector_id_missing:{index}')
            continue
        identities.append(sector_id)
        row_gaps = []
        for key in ('source', 'taxonomy', 'universe_id', 'data_date', 'unit'):
            if key in row and row[key] != snapshot.get(key):
                row_gaps.append(f'flow_mixed_{key}:{sector_id}')
        values: list[Any] = [row.get(key) for key in ('flow_1d', 'flow_5d', 'flow_20d')]
        if not all(finite(value) for value in values):
            row_gaps.append(f'flow_period_missing_or_invalid:{sector_id}')
        valid = not row_gaps and factor is not None
        converted: list[Any] = [float(value) * factor for value in values] if valid and factor is not None else [None, None, None]
        if valid and not all(finite(value) for value in converted):
            row_gaps.append(f'flow_conversion_overflow:{sector_id}')
            valid, converted = False, [None, None, None]
        state = 'unknown'
        if valid:
            one, five, twenty = converted
            state = ('outflow' if one < 0 else 'neutral' if one == 0 else
                     'one_day_pulse' if five <= 0 else
                     'positive_5d_20d' if twenty > 0 else 'rebound_5d')
        normalized.append({'sector_id': sector_id, 'sector_name': row.get('sector_name'),
                           **dict(zip(('flow_1d_yi', 'flow_5d_yi', 'flow_20d_yi'), converted)),
                           'window_sign_state': state})
        gaps.extend(row_gaps)
    if len(set(identities)) != len(identities):
        gaps.append('flow_duplicate_sector_id')
    if set(expected) != set(identities):
        gaps.append('flow_universe_coverage_mismatch')
    # Invalid date/cohort must not leak apparently valid classifications.
    if gaps:
        for row in normalized:
            row['window_sign_state'] = 'unknown'
    result.update(complete=not gaps, rows=normalized, expected_sector_count=len(expected),
                  actual_sector_count=len(identities), source=snapshot.get('source'),
                  taxonomy=snapshot.get('taxonomy'), universe_id=snapshot.get('universe_id'))
    if not gaps and snapshot.get('non_overlapping_membership') is True:
        result['scope_total_1d_yi'] = math.fsum(row['flow_1d_yi'] for row in normalized)
    else:
        result['aggregate_note'] = 'not_a_verified_disjoint_complete_sector_universe'
    return result, gaps


def review_history(history, sessions, taxonomy):
    gaps = []
    result = {'complete': False, 'stock_days': None, 'distinct_stocks': None, 'sectors': [], 'daily_counts': []}
    if not isinstance(history, list) or not history:
        return result, ['limit_history_missing']
    dates, cohorts, events = [], set(), []
    for row in history:
        if not isinstance(row, dict):
            gaps.append('limit_history_row_invalid')
            continue
        day = row.get('trade_date')
        if not isinstance(day, str):
            gaps.append('limit_history_date_invalid')
            continue
        dates.append(day)
        keys = ('source', 'source_ref', 'universe_id', 'taxonomy', 'definition_id')
        if any(not isinstance(row.get(k), str) or not row[k].strip() for k in keys):
            gaps.append('limit_history_provenance_missing')
            continue
        cohorts.add(tuple(row[k] for k in ('source', 'universe_id', 'taxonomy', 'definition_id')))
        if taxonomy and row['taxonomy'] != taxonomy:
            gaps.append('limit_flow_taxonomy_mismatch')
        if row.get('status') != 'available' or not isinstance(row.get('rows'), list):
            gaps.append('limit_history_source_unavailable')
            continue
        seen = set()
        for stock in row['rows']:
            if not isinstance(stock, dict):
                gaps.append('limit_stock_row_invalid')
                continue
            symbol, sector = stock.get('symbol'), stock.get('sector_id')
            if not isinstance(symbol, str) or not re.fullmatch(r'[0-9]{6}\.(SH|SZ|BJ)', symbol):
                gaps.append('limit_stock_identity_invalid')
                continue
            if not isinstance(sector, str) or not sector:
                gaps.append('limit_sector_id_missing')
                continue
            if symbol in seen:
                gaps.append('limit_stock_duplicate_within_day')
            seen.add(symbol)
            events.append((day, symbol, sector))
    if len(set(dates)) != len(dates) or set(dates) != set(sessions) or len(sessions) != 5:
        gaps.append('limit_history_session_coverage_mismatch')
    if len(cohorts) != 1:
        gaps.append('limit_history_mixed_cohort')
    if gaps:
        return result, sorted(set(gaps))
    by_sector = defaultdict(list)
    for event in events:
        by_sector[event[2]].append(event)
    counts = Counter(day for day, _, _ in events)
    result.update(complete=True, stock_days=len(events), distinct_stocks=len({s for _, s, _ in events}),
                  daily_counts=[{'trade_date': day, 'limit_up_count': counts[day]} for day in sessions],
                  sectors=[{'sector_id': sector, 'stock_days': len(items),
                            'distinct_stocks': len({s for _, s, _ in items})}
                           for sector, items in sorted(by_sector.items())])
    return result, []


def review_clock(payload):
    gaps, sessions = [], []
    if payload.get('schema_version') != 'a_share_review_input.v1':
        gaps.append('unsupported_input_schema')
    if payload.get('data_kind') not in ('fixture', 'source_capture'):
        gaps.append('data_kind_missing')
    try:
        trade_day = date.fromisoformat(payload['trade_date'])
        observed = datetime.fromisoformat(payload['observed_at'])
        session_end = datetime.fromisoformat(payload['session_end_at'])
        local_zone = ZoneInfo('Asia/Shanghai')
        if observed.tzinfo is None or observed.astimezone(ZoneInfo('Asia/Shanghai')).date() < trade_day:
            raise ValueError('invalid observation clock')
        if session_end.tzinfo is None or session_end.astimezone(local_zone).date() != trade_day or observed < session_end:
            raise ValueError('observation precedes the declared session close')
        if observed > datetime.now(observed.tzinfo):
            raise ValueError('future observation clock')
        calendar = payload['calendar']
        if not isinstance(calendar, dict):
            raise ValueError('invalid calendar shape')
        days = calendar['trading_days']
        if not isinstance(calendar.get('source_ref'), str) or not calendar['source_ref'].strip():
            raise ValueError('calendar source missing')
        if not isinstance(days, list) or any(not isinstance(d, str) for d in days):
            raise ValueError('invalid calendar')
        if len(set(days)) != len(days) or days != sorted(days) or payload['trade_date'] not in days:
            raise ValueError('calendar coverage/order invalid')
        for day in days:
            if date.fromisoformat(day).weekday() >= 5:
                raise ValueError('weekend is not an A-share trading session')
        sessions = [day for day in days if day <= payload['trade_date']][-5:]
        if len(sessions) < 5:
            gaps.append('calendar_five_sessions_missing')
    except (KeyError, TypeError, ValueError):
        gaps.append('date_clock_or_calendar_invalid')
    if payload.get('session_phase') != 'post_close':
        gaps.append('session_not_post_close')
    return sessions, gaps


def review_threshold(check, trade_date):
    out = {'name': check.get('name'), 'state': 'unknown', 'robust': False,
           'margin': None, 'source_spread': None, 'primary_pass': None}
    value, threshold, operator = check.get('value'), check.get('threshold'), check.get('operator')
    keys = ('name', 'unit', 'definition_id', 'source', 'source_ref')
    if (not finite(value) or not finite(threshold) or operator not in ('ge', 'le')
            or any(not isinstance(check.get(k), str) or not check[k].strip() for k in keys)
            or check.get('data_date') != trade_date):
        return out
    primary_pass = value >= threshold if operator == 'ge' else value <= threshold
    margin = abs(value - threshold)
    out.update(margin=margin, primary_pass=primary_pass, state='unassessed')
    alternatives = check.get('alternatives', [])
    if not alternatives:
        return out
    if not isinstance(alternatives, list):
        return dict(out, state='not_comparable')
    seen_sources = {check['source']}
    for row in alternatives:
        if (not isinstance(row, dict) or not finite(row.get('value'))
                or any(row.get(k) != check.get(k) for k in ('definition_id', 'unit', 'data_date'))
                or any(not isinstance(row.get(k), str) or not row[k].strip() for k in ('source', 'source_ref'))):
            return dict(out, state='not_comparable')
        if row['source'] in seen_sources:
            return dict(out, state='not_comparable')
        seen_sources.add(row['source'])
    spread = max(abs(row['value'] - value) for row in alternatives)
    flips = any((row['value'] >= threshold if operator == 'ge' else row['value'] <= threshold) != primary_pass
                for row in alternatives)
    sensitive = flips or margin <= spread
    return dict(out, source_spread=spread, state='boundary_sensitive' if sensitive else 'stable_on_observed_sources',
                robust=not sensitive)


def review_leader(row, trade_date):
    result = {k: row.get(k) for k in ('symbol', 'board_height', 'turnover_pct', 'one_price_board', 'source_ref')}
    result['state'] = 'unknown'
    if (not isinstance(row.get('symbol'), str) or not re.fullmatch(r'[0-9]{6}\.(SH|SZ|BJ)', row['symbol'])
            or not isinstance(row.get('board_height'), int) or isinstance(row['board_height'], bool)
            or row['board_height'] < 1 or type(row.get('one_price_board')) is not bool
            or not finite(row.get('turnover_pct')) or row['turnover_pct'] < 0
            or not isinstance(row.get('source_ref'), str) or not row['source_ref'].strip()
            or row.get('data_date') != trade_date):
        return result
    result['state'] = 'height_not_participation' if row['one_price_board'] else 'requires_turnover_context'
    return result


def build_review(payload: dict) -> dict:
    if not isinstance(payload, dict):
        raise ValueError('Input must be a JSON object')
    applied = payload.get('market') == 'A' and payload.get('intent') in ('post_close_review', 'sentiment_review')
    output = {
        'schema_version': 'a_share_post_close_review.v1',
        'status': 'partial' if applied else 'not_applicable',
        'applied': applied,
        'market': payload.get('market'),
        'module_signals': [],
        'no_order_execution': True,
        'may_raise_action_or_position': False,
        'may_write_formal_conclusion': False,
    }
    if not applied:
        return output
    sessions, gaps = review_clock(payload)
    clock_invalid = bool(gaps)
    flows, flow_gaps = review_flows(payload.get('flow_snapshot'), payload.get('trade_date'))
    taxonomy = flows.get('taxonomy')
    ecology, history_gaps = review_history(payload.get('limit_up_history'), sessions, taxonomy)
    if gaps:
        flows.update(complete=False, scope_total_1d_yi=None)
        for row in flows['rows']:
            row['window_sign_state'] = 'unknown'
        ecology.update(complete=False, stock_days=None, distinct_stocks=None, sectors=[], daily_counts=[])
    gaps += flow_gaps + history_gaps
    optional = {}
    for input_key, output_key, reviewer in [('threshold_checks', 'threshold_sensitivity', review_threshold),
                                          ('leader_observations', 'leader_participation', review_leader)]:
        rows = payload.get(input_key, [])
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            gaps.append(f'{input_key}_invalid')
            rows = []
        optional[output_key] = [reviewer(row, payload.get('trade_date')) for row in rows]
        if clock_invalid:
            for row in optional[output_key]:
                row['state'] = 'unknown'
                if 'robust' in row:
                    row.update(robust=False, primary_pass=None, margin=None, source_spread=None)
        if any(row['state'] == 'unknown' for row in optional[output_key]):
            gaps.append(f'{input_key}_incomplete')
    output.update(optional)
    output.update({key: payload.get(key) for key in ('trade_date', 'observed_at', 'session_end_at', 'session_phase')})
    output.update(sector_flow=flows, limit_ecology=ecology, data_gaps=sorted(set(gaps)),
                  status='partial' if gaps else 'ok', data_contract_complete=not gaps,
                  report_mode='post_close_date_scoped' if payload.get('session_phase') == 'post_close' else 'intraday_preview',
                  evidence_status='fixture_only' if payload.get('data_kind') == 'fixture' else 'requires_evidence_verification')
    return output


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', required=True, type=Path, help='Local a_share_review_input.v1 JSON; no live fetch')
    parser.add_argument('--pretty', action='store_true')
    args = parser.parse_args(argv)
    try:
        raw = args.input.read_bytes()
        result = build_review(json.loads(raw))
        result['input_sha256'] = hashlib.sha256(raw).hexdigest()
        result['calculator_sha256'] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        print(json.dumps(result, ensure_ascii=False, allow_nan=False, indent=2 if args.pretty else None))
        return 3 if not result['applied'] else 0 if result['data_contract_complete'] else 1
    except (OSError, ValueError, TypeError, OverflowError) as exc:
        print(json.dumps({'status': 'invalid_input', 'error': str(exc), 'no_order_execution': True}))
        return 2


if __name__ == '__main__':
    sys.exit(main())
