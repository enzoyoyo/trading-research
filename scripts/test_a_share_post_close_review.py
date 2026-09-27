#!/usr/bin/env python3
"""Offline boundary and adversarial tests; all market data are fixtures."""
import copy
import importlib.util
import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / 'scripts/a_share_post_close_review.py'
TEMPLATE = ROOT / 'templates/a-share-post-close-review-input.json'


def load_module():
    spec = importlib.util.spec_from_file_location('a_share_post_close_review', MODULE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ReviewTests(unittest.TestCase):
    def test_scope_rejects_neighbor_markets_and_intents(self):
        self.assertTrue(MODULE.is_file(), 'A-share-only review entry point is missing')
        module = load_module()
        for market, intent in [('US', 'post_close_review'), ('HK', 'post_close_review'),
                               ('OKX', 'post_close_review'), ('unknown', 'post_close_review'),
                               ('A', 'long_term_fundamental'), ('A', 'single_stock_trade'),
                               ('A', 'macro_only'), ('A,H', 'post_close_review')]:
            with self.subTest(market=market, intent=intent):
                result = module.build_review({'market':market, 'intent':intent})
                self.assertEqual(result['status'], 'not_applicable')
                self.assertFalse(result['applied'])
                self.assertEqual(result['module_signals'], [])
                self.assertTrue(result['no_order_execution'])
                self.assertNotIn('action_level', result)

    def test_flow_contract_normalizes_once_and_rejects_incomplete_cohorts(self):
        module = load_module()
        payload = json.loads(TEMPLATE.read_text())
        result = module.build_review(payload)
        flow = result.get('sector_flow', {})
        self.assertEqual(flow.get('scope_total_1d_yi'), 1.0)
        self.assertEqual(flow['rows'][0]['flow_20d_yi'], 4.0)
        self.assertEqual(flow['rows'][0]['window_sign_state'], 'positive_5d_20d')
        self.assertEqual(flow['rows'][1]['window_sign_state'], 'outflow')
        self.assertFalse(result['may_raise_action_or_position'])
        self.assertEqual(result['evidence_status'], 'fixture_only')
        self.assertEqual(payload, json.loads(TEMPLATE.read_text()))
        for mutation in ['null', 'nonfinite', 'bool', 'mixed_source', 'mixed_unit', 'duplicate',
                         'missing_sector', 'extra_sector', 'stale', 'unknown_unit']:
            bad = copy.deepcopy(payload)
            snap = bad['flow_snapshot']
            if mutation == 'null': snap['rows'][0]['flow_20d'] = None
            if mutation == 'nonfinite': snap['rows'][0]['flow_5d'] = float('nan')
            if mutation == 'bool': snap['rows'][0]['flow_1d'] = True
            if mutation == 'mixed_source': snap['rows'][0]['source'] = 'another_vendor'
            if mutation == 'mixed_unit': snap['rows'][0]['unit'] = 'CNY'
            if mutation == 'duplicate': snap['rows'].append(copy.deepcopy(snap['rows'][0]))
            if mutation == 'missing_sector': snap['rows'].pop()
            if mutation == 'extra_sector': snap['rows'][0]['sector_id'] = 'unmapped-sector'
            if mutation == 'stale': snap['data_date'] = '2026-09-24'
            if mutation == 'unknown_unit': snap['unit'] = 'unknown'
            with self.subTest(mutation=mutation):
                got = module.build_review(bad)
                self.assertFalse(got['sector_flow']['complete'])
                self.assertIsNone(got['sector_flow']['scope_total_1d_yi'])
                self.assertTrue(got['data_gaps'])
        overlapping = copy.deepcopy(payload)
        overlapping['flow_snapshot']['non_overlapping_membership'] = False
        self.assertIsNone(module.build_review(overlapping)['sector_flow']['scope_total_1d_yi'])

    def test_history_counts_stock_days_not_distinct_stocks_and_checks_clock(self):
        module = load_module()
        payload = json.loads(TEMPLATE.read_text())
        history = []
        for day in payload['calendar']['trading_days']:
            history.append({'trade_date': day, 'source': 'fixture_limit_vendor',
                            'source_ref': 'fixture:daily-limit-pool', 'status': 'available',
                            'taxonomy': 'fixture_industry_v1', 'universe_id': 'fixture_limit_universe',
                            'definition_id': 'close_sealed_no_st_fixture',
                            'rows': [{'symbol': '000001.SZ', 'sector_id': 'fixture-sector-a'}]})
        payload['limit_up_history'] = history
        result = module.build_review(payload)
        ecology = result.get('limit_ecology', {})
        self.assertEqual(ecology.get('stock_days'), 5)
        self.assertEqual(ecology['distinct_stocks'], 1)
        self.assertEqual(ecology['sectors'][0]['distinct_stocks'], 1)
        self.assertTrue(result['data_contract_complete'])
        for mutation in ['missing_day', 'duplicate_stock', 'mixed_definition', 'mixed_taxonomy',
                         'wrong_symbol', 'empty_error', 'bad_clock', 'bad_calendar', 'schema']:
            bad = copy.deepcopy(payload)
            if mutation == 'missing_day': bad['limit_up_history'].pop()
            if mutation == 'duplicate_stock': bad['limit_up_history'][0]['rows'] *= 2
            if mutation == 'mixed_definition': bad['limit_up_history'][0]['definition_id'] = 'another_definition'
            if mutation == 'mixed_taxonomy': bad['limit_up_history'][0]['taxonomy'] = 'another_taxonomy'
            if mutation == 'wrong_symbol': bad['limit_up_history'][0]['rows'][0]['symbol'] = 'AAPL.US'
            if mutation == 'empty_error': bad['limit_up_history'][0].update(status='error', rows=[])
            if mutation == 'bad_clock': bad['observed_at'] = '2026-09-24T16:00:00+08:00'
            if mutation == 'bad_calendar': bad['calendar']['source_ref'] = ''
            if mutation == 'schema': bad['schema_version'] = 'unknown'
            with self.subTest(mutation=mutation):
                got = module.build_review(bad)
                self.assertFalse(got['data_contract_complete'])
                self.assertTrue(got['data_gaps'])
        preview = copy.deepcopy(payload)
        preview['session_phase'] = 'intraday'
        self.assertFalse(module.build_review(preview)['data_contract_complete'])
        self.assertEqual(module.build_review(preview)['report_mode'], 'intraday_preview')
        empty = copy.deepcopy(payload)
        for row in empty['limit_up_history']: row['rows'] = []
        self.assertEqual(module.build_review(empty)['limit_ecology']['stock_days'], 0)

    def test_boundary_sensitivity_and_nominal_board_height(self):
        module = load_module()
        payload = json.loads(TEMPLATE.read_text())
        payload['threshold_checks'] = [{
            'name': 'fixture_limit_up_count', 'value': 63, 'threshold': 60, 'operator': 'ge',
            'unit': 'count', 'definition_id': 'fixture_same_definition',
            'source': 'fixture_vendor_a', 'source_ref': 'fixture:primary', 'data_date': payload['trade_date'],
            'alternatives': [{'value': 57, 'definition_id': 'fixture_same_definition', 'unit': 'count',
                              'source': 'fixture_vendor_b', 'source_ref': 'fixture:alternative',
                              'data_date': payload['trade_date']}]}]
        payload['leader_observations'] = [{'symbol':'000001.SZ', 'board_height':6,
                                         'one_price_board':True, 'turnover_pct':0.74,
                                         'data_date':payload['trade_date'], 'source_ref':'fixture:leader'}]
        result = module.build_review(payload)
        self.assertTrue(result.get('threshold_sensitivity'), 'threshold review missing')
        check = result['threshold_sensitivity'][0]
        self.assertEqual(check['margin'], 3)
        self.assertEqual(check['source_spread'], 6)
        self.assertEqual(check['state'], 'boundary_sensitive')
        self.assertFalse(check['robust'])
        self.assertEqual(result['leader_participation'][0]['state'], 'height_not_participation')
        del payload['threshold_checks'][0]['alternatives']
        check = module.build_review(payload)['threshold_sensitivity'][0]
        self.assertEqual(check['state'], 'unassessed')
        self.assertFalse(check['robust'])
        payload['threshold_checks'][0]['alternatives'] = [{'value':58, 'definition_id':'different',
            'source':'vendor_b', 'source_ref':'fixture:b', 'unit':'count', 'data_date':payload['trade_date']}]
        self.assertEqual(module.build_review(payload)['threshold_sensitivity'][0]['state'], 'not_comparable')
        payload['threshold_checks'][0]['value'] = None
        self.assertEqual(module.build_review(payload)['threshold_sensitivity'][0]['state'], 'unknown')
        self.assertEqual(module.build_review(payload)['module_signals'], [])

    def test_cli_template_has_a_real_complete_read_only_path(self):
        run = subprocess.run([sys.executable, str(MODULE), '--input', str(TEMPLATE)], capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stderr or run.stdout)
        self.assertTrue(run.stdout.strip(), 'CLI must emit an actual review artifact')
        result = json.loads(run.stdout)
        self.assertTrue(result['data_contract_complete'])
        self.assertEqual(result['evidence_status'], 'fixture_only')
        self.assertEqual(len(result['input_sha256']), 64)
        self.assertTrue(result['no_order_execution'])
        self.assertEqual(result['trade_date'], '2026-09-25')
        self.assertEqual(result['observed_at'], '2026-09-25T16:00:00+08:00')
        self.assertEqual(result['session_end_at'], '2026-09-25T15:00:00+08:00')

    def test_clock_failures_cannot_leak_valid_classification(self):
        module = load_module()
        payload = json.loads(TEMPLATE.read_text())
        self.assertFalse(module.build_review(payload).get('may_write_formal_conclusion', True))
        for mutation in ['before_close', 'missing_close', 'bad_calendar_shape', 'weekend']:
            bad = copy.deepcopy(payload)
            if mutation == 'before_close': bad['observed_at'] = '2026-09-25T09:45:00+08:00'
            if mutation == 'missing_close': bad.pop('session_end_at', None)
            if mutation == 'bad_calendar_shape': bad['calendar'] = []
            if mutation == 'weekend': bad['calendar']['trading_days'][0] = '2026-09-20'
            with self.subTest(mutation=mutation):
                got = module.build_review(bad)
                self.assertFalse(got['data_contract_complete'])
                self.assertFalse(got['sector_flow']['complete'])
                self.assertIsNone(got['sector_flow']['scope_total_1d_yi'])
                self.assertTrue(all(r['window_sign_state'] == 'unknown' for r in got['sector_flow']['rows']))
                self.assertFalse(got['limit_ecology']['complete'])

    def test_threshold_repeated_source_is_not_independent(self):
        module = load_module()
        payload = json.loads(TEMPLATE.read_text())
        check = payload['threshold_checks'][0]
        check['value'] = 100
        check['alternatives'][0].update(value=99, source=check['source'])
        out = module.build_review(payload)['threshold_sensitivity'][0]
        self.assertEqual(out['state'], 'not_comparable')
        self.assertFalse(out['robust'])

    def test_malformed_optional_and_numeric_inputs_fail_closed(self):
        module = load_module()
        template = json.loads(TEMPLATE.read_text())
        for value in [None, [], {}, True, float('inf'), 10 ** 1000]:
            for field in ['unit', 'flow_1d']:
                bad = copy.deepcopy(template)
                bad['flow_snapshot']['rows'][0][field] = value
                with self.subTest(field=field, value_type=type(value).__name__):
                    got = module.build_review(bad)
                    self.assertFalse(got['data_contract_complete'])
        for height in [-1, 0, 1.5, True]:
            bad = copy.deepcopy(template)
            bad['leader_observations'][0]['board_height'] = height
            self.assertEqual(module.build_review(bad)['leader_participation'][0]['state'], 'unknown')
        bad = copy.deepcopy(template)
        bad['observed_at'] = '2026-09-25T09:30:00+08:00'
        bad['threshold_checks'][0]['value'] = 100
        out = module.build_review(bad)
        self.assertEqual(out['threshold_sensitivity'][0]['state'], 'unknown')
        self.assertFalse(out['threshold_sensitivity'][0]['robust'])


if __name__ == '__main__':
    unittest.main()
