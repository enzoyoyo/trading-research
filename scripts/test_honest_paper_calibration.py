"""Synthetic contracts only; no historic proxy observations are relabelled."""
import copy
import json
import sqlite3
import unittest
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
import paper_outcome_calibration_feed as feed
import calibration_scorecard as card


def entry(proposal='p1', horizon='swing_days'):
    return {'status': 'open', 'symbol': 'ABC.US', 'side': 'Buy', 'proposal_id': proposal, 'quantity': 10, 'timestamp_utc': '2026-01-01T10:00:00Z', 'paper_prediction_contract': {'schema_version': 1, 'prediction_id': 'synthetic-' + proposal, 'proposal_id': proposal, 'symbol': 'ABC.US', 'p': .62, 'as_of': '2026-01-01T09:00:00Z', 'frozen_at': '2026-01-01T09:30:00Z', 'horizon_id': horizon, 'target': 'net_pnl_positive', 'settlement': 'full_lifecycle', 'strategy_version': 'fixture-strategy-v1', 'model_version': 'fixture-model-v1', 'basis': 'Synthetic held-out forecast fixture', 'probability_kind': 'ex_ante_forecast', 'evidence_refs': ['fixture:training-cutoff-2025-12-31']}}


def close():
    return {'status': 'closed', 'symbol': 'ABC.US', 'side': 'Sell', 'proposal_id': 'exit', 'quantity': 10, 'timestamp_utc': '2026-01-02T10:00:00Z', 'r_multiple': 1.2, 'net_pnl': -2, 'net_pnl_currency': 'USD', 'costs_included': True, 'cost_evidence_refs': ['fixture:broker-fees']}


class HonestPaperCalibrationTests(unittest.TestCase):
    def test_contract_positive_and_gross_win_net_loss(self):
        samples, _ = feed.pair_samples([entry(), close()], [])
        self.assertEqual(len(samples), 1)
        self.assertEqual(samples[0]['outcome'], 0)
        self.assertEqual(samples[0]['predicted_p'], .62)

    def test_invalid_contracts_cannot_enter_samples_or_pending(self):
        mutations = [('p', 0), ('p', 1), ('p', float('nan')), ('p', float('inf')), ('p', True), ('p', '.62'), ('as_of', 'broken'), ('as_of', '2026-01-01T09:00:00'), ('as_of', '2099-01-01T00:00:00Z'), ('frozen_at', '2026-01-02T00:00:00Z'), ('as_of', '2026-01-01T09:45:00Z'), ('proposal_id', 'other'), ('symbol', 'XYZ.US'), ('target', 'gross_return_positive'), ('settlement', 'first_partial_exit'), ('horizon_id', ''), ('basis', ''), ('model_version', ''), ('strategy_version', ''), ('prediction_id', ''), ('evidence_refs', []), ('probability_kind', 'score_proxy')]
        for key, value in mutations:
            row = entry(); row['paper_prediction_contract'][key] = value
            with self.subTest(key=key, value=value):
                self.assertEqual(feed.pair_samples([row, close()], [])[0], [])
                self.assertEqual(feed.collect_pending_predictions([row], [], {}), [])

    def test_exit_commentary_and_mutable_artifacts_cannot_supply_forecast(self):
        row = entry(); contract = row.pop('paper_prediction_contract')
        row['decision_fusion'] = {'win_rate_proxy': .62}
        row['review_note'] = {'paper_prediction_contract': contract, 'predicted_p': .9}
        exit_row = close(); exit_row['paper_prediction_contract'] = contract
        index = {'p1': {'predicted_p': .99, 'prediction_field': 'predicted_p'}}
        self.assertEqual(feed.pair_samples([row, exit_row], [], index)[0], [])
        self.assertEqual(feed.prediction_from(row), (None, None))

    def test_net_receipts_required_for_every_partial_fill(self):
        for missing in ('net_pnl', 'costs_included', 'cost_evidence_refs', 'net_pnl_currency', 'quantity'):
            row = close(); row.pop(missing)
            with self.subTest(missing=missing):
                self.assertEqual(feed.pair_samples([entry(), row], [])[0], [])
        row = close(); row['net_pnl'] = float('nan')
        self.assertEqual(feed.pair_samples([entry(), row], [])[0], [])
        first = close(); first.update(status='partial_closed', quantity=4)
        final = close(); final.update(proposal_id='exit2', quantity=6, timestamp_utc='2026-01-03T10:00:00Z')
        self.assertEqual(len(feed.pair_samples([entry(), first, final], [])[0]), 1)
        final.pop('cost_evidence_refs')
        self.assertEqual(feed.pair_samples([entry(), first, final], [])[0], [])

    def test_scorecard_excludes_legacy_and_unverified_without_rewriting(self):
        conn = sqlite3.connect(':memory:'); conn.row_factory = sqlite3.Row
        feed.ensure_table(conn)
        samples, _ = feed.pair_samples([entry(), close()], [])
        feed.write_samples(conn, samples)
        good = samples[0]
        for suffix, field in [('proxy', 'decision_fusion.win_rate_proxy'), ('unverified', 'predicted_p')]:
            bad = copy.deepcopy(good); bad.update(sample_id=suffix, trade_lifecycle_id=suffix, prediction_field=field, source_payload={})
            feed.write_samples(conn, [bad])
        before = list(conn.execute('SELECT * FROM calibration_samples_paper'))
        audit = card.paper_sample_audit(conn, 36)
        self.assertEqual((audit['eligible'], audit['legacy'], audit['excluded']), (1, 1, 2))
        self.assertEqual(before, list(conn.execute('SELECT * FROM calibration_samples_paper')))
        self.assertEqual(audit['pairs'][0]['actual'], 0)

    def test_pending_history_payload_is_immutable_and_legacy_not_pending(self):
        conn = sqlite3.connect(':memory:'); conn.row_factory = sqlite3.Row
        pending = feed.collect_pending_predictions([entry()], [], {})
        feed.write_pending_predictions(conn, pending, [])
        before = conn.execute('SELECT source_payload_json FROM calibration_pending_paper_predictions').fetchone()[0]
        changed = copy.deepcopy(pending); changed[0]['source_payload']['paper_prediction_contract']['p'] = .9
        feed.write_pending_predictions(conn, changed, [])
        self.assertEqual(before, conn.execute('SELECT source_payload_json FROM calibration_pending_paper_predictions').fetchone()[0])
        feed.write_pending_predictions(conn, [], [])
        self.assertEqual(conn.execute('SELECT count(*) FROM calibration_pending_paper_predictions').fetchone()[0], 1)
        feed.write_pending_predictions(conn, [], [{'decision_ref': 'p1'}])
        self.assertEqual(card.paper_pending_stats(conn)['settled'], 1)

    def test_horizons_and_versions_are_not_pooled(self):
        from trading_memory_core import connect
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            conn = connect(Path(td) / 'isolated.sqlite')
            for proposal, horizon in [('p1', 'swing_days'), ('p2', 'position_months')]:
                samples, _ = feed.pair_samples([entry(proposal, horizon), close()], [])
                feed.write_samples(conn, samples)
            result = card.build_scorecard(conn, 36, 1, 'paper')
            self.assertEqual(len(result['paper_buckets']), 2)
            self.assertIsNone(result['brier_score'])
            self.assertFalse(result['materiality_eligible'])
            conn.close()

if __name__ == '__main__':
    unittest.main()
