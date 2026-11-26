"""Synthetic CLI contracts in temporary SQLite; never market network or production DB."""
import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from datetime import datetime, timezone

import record_due_results as recorder
from memory_store import connect
from prediction_ledger import register_prediction
from test_prediction_ledger import payload


class PredictionsOnlyCLITests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'synthetic-memory.sqlite'
        conn = connect(self.path)
        self.assertTrue(register_prediction(conn, payload(), registered_at=datetime(2026, 1, 1, tzinfo=timezone.utc))['ok'])
        conn.close()

    def snapshot(self):
        conn = connect(self.path)
        try:
            return {'dump': '\n'.join(conn.iterdump()), 'predictions': [dict(row) for row in conn.execute('SELECT prediction_id,status FROM predictions')], 'outcomes': [dict(row) for row in conn.execute('SELECT * FROM prediction_outcomes')], 'legacy_results': conn.execute('SELECT COUNT(*) FROM results').fetchone()[0]}
        finally:
            conn.close()

    def cli(self, quote=None, now='2026-01-03T01:00:00Z', dry=False):
        argv = ['record_due_results.py', '--db', str(self.path), '--predictions-only', '--now', now, '--json']
        if dry:
            argv.append('--dry-run')
        out = io.StringIO()
        with patch.object(sys, 'argv', argv), patch.object(recorder, 'quote_with_fallback', return_value=quote), patch.object(recorder, 'run', side_effect=AssertionError('legacy run must not execute')) as legacy, contextlib.redirect_stdout(out):
            code = recorder.main()
            legacy.assert_not_called()
        return code, json.loads(out.getvalue())

    def test_predictions_only_settles_real_contract_and_is_idempotent(self):
        code, result = self.cli({'price': 102, 'symbol': 'TEST.US', 'source': 'synthetic'})
        self.assertEqual(code, 0)
        self.assertEqual(result['status'], 'prediction_contracts_only')
        self.assertTrue(result['no_order_execution'])
        self.assertEqual(result['prediction_ledger']['settled_count'], 1)
        snapshot = self.snapshot()
        self.assertEqual(len(snapshot['outcomes']), 1)
        self.assertEqual(snapshot['outcomes'][0]['actual'], 1)
        self.assertEqual(snapshot['legacy_results'], 0)
        again_code, again = self.cli({'price': 50, 'symbol': 'TEST.US', 'source': 'synthetic'})
        self.assertEqual(again_code, 0)
        self.assertEqual(again['prediction_ledger']['settled_count'], 0)
        self.assertEqual(self.snapshot()['dump'], snapshot['dump'])

    def test_dry_run_does_not_write_any_row(self):
        before = self.snapshot()['dump']
        code, result = self.cli({'price': 102, 'symbol': 'TEST.US', 'source': 'synthetic'}, dry=True)
        self.assertEqual(code, 0)
        self.assertTrue(result['dry_run'])
        self.assertTrue(result['prediction_ledger']['settled'][0]['dry_run'])
        self.assertEqual(self.snapshot()['dump'], before)
        self.assertEqual(self.snapshot()['predictions'][0]['status'], 'open')

    def test_missing_quote_does_not_create_failure_or_zero(self):
        before = self.snapshot()['dump']
        code, result = self.cli(None)
        self.assertEqual(code, 2)
        self.assertEqual(result['prediction_ledger']['settled_count'], 0)
        self.assertTrue(result['prediction_ledger']['fail_closed'])
        self.assertEqual(self.snapshot()['dump'], before)

    def test_wrong_symbol_never_settles(self):
        code, result = self.cli({'price': 102, 'symbol': 'OTHER.US', 'source': 'synthetic'})
        self.assertEqual(code, 2)
        self.assertEqual(result['prediction_ledger']['identity_mismatch'], ['PR-TEST-1'])
        self.assertEqual(self.snapshot()['outcomes'], [])

    def test_late_quote_marks_unresolved_not_fake_outcome(self):
        code, result = self.cli({'price': 150, 'symbol': 'TEST.US', 'source': 'synthetic'}, now='2026-02-01T00:00:00Z')
        self.assertEqual(code, 2)
        self.assertEqual(result['prediction_ledger']['stale_settlement_excluded'], ['PR-TEST-1'])
        self.assertEqual(self.snapshot()['predictions'][0]['status'], 'unresolved')
        self.assertEqual(self.snapshot()['outcomes'], [])
        self.assertEqual(self.snapshot()['legacy_results'], 0)

    def test_stale_dry_run_preserves_open_contract(self):
        before = self.snapshot()['dump']
        code, result = self.cli({'price': 150, 'symbol': 'TEST.US', 'source': 'synthetic'}, now='2026-02-01T00:00:00Z', dry=True)
        self.assertEqual(code, 2)
        self.assertEqual(result['prediction_ledger']['settled_count'], 0)
        self.assertEqual(self.snapshot()['dump'], before)

    def test_real_subprocess_cli_future_contract_does_not_fetch_or_write(self):
        before = self.snapshot()['dump']
        result = subprocess.run([sys.executable, str(Path(recorder.__file__)), '--db', str(self.path), '--predictions-only', '--dry-run', '--now', '2026-01-02T00:00:00Z', '--json'], capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['prediction_ledger']['due_count'], 0)
        self.assertEqual(self.snapshot()['dump'], before)

    def test_invalid_cli_clock_fails_before_db(self):
        before = self.snapshot()['dump']
        code, result = self.cli(now='2026-01-03T01:00:00')
        self.assertEqual(code, 1)
        self.assertEqual(result['error'], 'invalid_or_timezone_naive_--now')
        self.assertEqual(self.snapshot()['dump'], before)


if __name__ == '__main__':
    unittest.main()
