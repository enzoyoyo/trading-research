"""Offline synthetic contract tests; no live-market or execution claims."""
from __future__ import annotations
import copy
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
import decision_compiler
import strategy_orchestrator as subject
import research_run
import a_stock_data_bridge

NOW = datetime(2026, 9, 5, 8, tzinfo=timezone.utc)

def synthetic_candidate():
    context = dict(query_tier='T2', intent='open', has_position=False,
                   as_of=NOW.isoformat(), symbol='SYNTH.US', horizon_id='swing_days',
                   instrument='equity', required_modules=list(subject.GLOBAL_GATES))
    signals = [dict(module=m, max_action_level='L2', position_multiplier=1.0,
                    hard_veto=False, evidence_refs=['SYNTHETIC-' + m],
                    observed_at=NOW.isoformat(), stale_after=(NOW + timedelta(hours=1)).isoformat())
               for m in subject.GLOBAL_GATES]
    return dict(symbol='SYNTH.US', horizon_id='swing_days', instrument='equity',
                strategy_id='trend_following', decision_request=dict(
                    schema_version='decision_request.v2', decision_context=context, module_signals=signals))

class StrategyMandateTests(unittest.TestCase):
    def test_invalid_compiler_size_is_contract_gap_not_crash_or_ready(self):
        for value in (None, True, '1', float('nan'), float('inf')):
            raw={'contract_status':'strict_pass','ok':True,'entry_permission':'BUILD','final_position_multiplier':value}
            self.assertEqual(subject.classify_blocker(raw),'contract_gap')

    def test_deterministic_and_both_regimes_scan(self):
        for regime in ('normal', 'active_deleveraging'):
            args = dict(market='US', horizon_id='swing_days', risk_regime=regime)
            first = subject.build_strategy_mandate(**args)
            self.assertEqual(first, subject.build_strategy_mandate(**args))
            self.assertTrue(first['lanes'])
            self.assertTrue(first['scan_continues_during_defence'])
        defensive = subject.build_strategy_mandate(horizon_id='swing_days', risk_regime='active_deleveraging')
        self.assertEqual(defensive['lanes'][0]['strategy_id'], 'defensive_relative_strength')

    def test_unknown_risk_grants_nothing(self):
        plan = subject.build_strategy_mandate(horizon_id='swing_days', risk_regime='imaginary')
        self.assertEqual(plan['risk_context'], 'unknown')
        self.assertFalse(plan['risk_context_verified'])
        self.assertIsNone(plan['probability'])
        self.assertIsNone(plan['position_multiplier'])
        self.assertIn('fresh_risk_snapshot_required', plan['data_gaps'])
        self.assertTrue(all(x['entry_permission'] is None for x in plan['lanes']))

    def test_horizon_applicability(self):
        for horizon in subject.HORIZONS:
            plan = subject.build_strategy_mandate(horizon_id=horizon)
            self.assertTrue(all(horizon in x['horizons'] for x in plan['lanes']))
        self.assertEqual([x['strategy_id'] for x in subject.build_strategy_mandate(horizon_id='overnight_cto')['lanes']], ['overnight_cto'])

    def test_a_share_cannot_enter_us_campaign(self):
        for market in ('CN', 'A', 'A_SHARE', 'HK'):
            plan = subject.build_strategy_mandate(market=market, horizon_id='swing_days')
            self.assertEqual(plan['lanes'], [])
            self.assertIn('us_campaign_not_applicable_use_market_router', plan['data_gaps'])

    def test_options_require_separate_questions(self):
        options = subject.build_strategy_mandate(instrument='option', horizon_id='swing_days')
        self.assertIn('option_identity', options['instrument_questions'])
        self.assertIn('cost_edge_and_iv_crush', options['instrument_questions'])
        self.assertEqual(subject.build_strategy_mandate(instrument='equity')['instrument_questions'], [])

class StrictReplayTests(unittest.TestCase):
    def review(self, candidate):
        return subject.compile_research_candidates([candidate], now=NOW)['candidates'][0]

    def test_synthetic_fresh_baseline_is_ready(self):
        row = self.review(synthetic_candidate())
        self.assertEqual(row['blocker_class'], 'ready', row)
        self.assertEqual(row['compilation']['contract_status'], 'strict_pass')

    def test_all_constraints_pass_unchanged_to_real_compiler(self):
        candidate = synthetic_candidate()
        candidate['decision_request']['module_signals'].append(dict(
            candidate['decision_request']['module_signals'][0], position_multiplier=0.0,
            sub_framework='synthetic_discovered_zero_cap'))
        original = copy.deepcopy(candidate)
        with patch.object(decision_compiler, 'compile_payload', wraps=decision_compiler.compile_payload) as compile_spy:
            row = self.review(candidate)
        self.assertEqual(compile_spy.call_args.args[0], original['decision_request'])
        self.assertEqual(candidate, original)
        self.assertEqual(row['compilation']['final_position_multiplier'], 0.0)
        self.assertNotEqual(row['blocker_class'], 'ready')

    def test_hard_veto_stays_blocked(self):
        candidate = synthetic_candidate()
        candidate['decision_request']['module_signals'][0]['hard_veto'] = True
        row = self.review(candidate)
        self.assertTrue(row['compilation']['hard_veto'])
        self.assertEqual(row['compilation']['entry_permission'], 'BLOCK')
        self.assertNotEqual(row['blocker_class'], 'ready')

    def test_legacy_is_rejected_before_compile(self):
        candidate = synthetic_candidate()
        candidate['decision_request'].pop('schema_version')
        with patch.object(decision_compiler, 'compile_payload') as spy:
            row = self.review(candidate)
        spy.assert_not_called()
        self.assertIn('strict_decision_request_required', row['errors'])

    def test_legacy_horizon_alias_and_conflict(self):
        candidate = synthetic_candidate()
        context = candidate['decision_request']['decision_context']
        context['horizon'] = context.pop('horizon_id')
        self.assertEqual(self.review(candidate)['blocker_class'], 'ready')
        context['horizon_id'] = 'position_months'
        self.assertIn('candidate_horizon_id_binding_required', self.review(candidate)['errors'])

    def test_bindings_and_strategy_horizon_rejected(self):
        for key, bad in [('symbol', 'OTHER.US'), ('horizon_id', 'position_months'), ('instrument', 'option')]:
            candidate = synthetic_candidate()
            candidate['decision_request']['decision_context'][key] = bad
            self.assertIn('candidate_' + key + '_binding_required', self.review(candidate)['errors'])
        candidate = synthetic_candidate()
        candidate['horizon_id'] = candidate['decision_request']['decision_context']['horizon_id'] = 'intraday'
        self.assertIn('strategy_horizon_mismatch', self.review(candidate)['errors'])

class PlanningIntegrationTests(unittest.TestCase):
    def test_unobserved_scorecard_is_unknown(self):
        template = research_run.generate_scorecard_template('SYNTH.US', {})
        for section in ('factors', 'penalties'):
            self.assertTrue(template[section])
            self.assertTrue(all(value is None for value in template[section].values()))

    def test_explicit_financial_source_preserves_missing_quote_clock(self):
        fixture = dict(ok=True, source='synthetic-financial-api', observed_at=NOW.isoformat(),
                       data=[dict(symbol='600000.SH', price=1.0, quote_timestamp=None)],
                       data_gaps=['quote_timestamp_missing'])
        with patch('financial_api_bridge.fetch', return_value=copy.deepcopy(fixture)) as fetch, \
             patch.object(a_stock_data_bridge, 'build_opener'), \
             patch.object(a_stock_data_bridge, 'cmd_quote') as tencent, \
             patch.object(a_stock_data_bridge, 'emit') as emit:
            code = a_stock_data_bridge.main(['quote', '600000.SH', '--source', 'financial_api', '--json'])
        self.assertEqual(code, 0)
        fetch.assert_called_once_with('quote', options={'symbols': ['600000.SH']})
        tencent.assert_not_called()
        self.assertEqual(emit.call_args.args[0], fixture)

if __name__ == '__main__':
    unittest.main()
