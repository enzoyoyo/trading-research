#!/usr/bin/env python3
"""Synthetic offline regressions for structured paper-learning consumption."""
from __future__ import annotations

import argparse
from contextlib import redirect_stdout
from copy import deepcopy
from datetime import datetime, timezone
import io
import json
import os
import subprocess
import sys
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import paper_learning_consumer as consumer
import self_optimization_check as check
import self_optimization_ledger as ledger
import eval_candidate_generator as generator

NOW = datetime(2026, 9, 5, 6, tzinfo=timezone.utc)


def packet(count=10):
    reviews = [{"trade_lifecycle_id": f"paper:SYNTHETIC.US:fixture-{i}",
                "strategy": "fixture_strategy", "outcome": "failure", "r_multiple": -1,
                "opened_at": "2026-01-01T00:00:00Z", "completed_at": "2026-01-02T00:00:00Z"}
               for i in range(count)]
    evidence = {"real": {"trades": count, "known_outcomes": count, "unknown_outcomes": 0,
                         "r_sample_count": count, "avg_r": "-1"},
                "lifecycle_ids": [row["trade_lifecycle_id"] for row in reviews],
                "out_of_sample": {"num_trades": 200, "profit_factor": 2},
                "replay_as_of": "2026-01-03T00:00:00Z", "policy_version": "synthetic-only"}
    candidate = {"schema_version": consumer.CANDIDATE_SCHEMA, "strategy": "fixture_strategy",
                 "pattern": "negative_real_expectancy_vs_positive_oos", "status": "review_required",
                 "evidence": evidence, "auto_apply": False, "risk_relaxation_allowed": False,
                 "candidate_id": consumer.fingerprint({"strategy": "fixture_strategy", "evidence": evidence}),
                 "review_actions": ["Synthetic regression fixture, not market evidence"]}
    return {"schema_version": "paper_learning_packet.v2", "generated_at_utc": "2026-01-03T00:00:00Z",
            "materiality_gate": {"enough_for_trading_research_skill_upgrade": True,
                                 "enough_for_policy_sizing_change": False},
            "closed_trade_reviews": reviews,
            "learning_evidence": {"schema_version": consumer.EVIDENCE_SCHEMA, "failure_patterns": [candidate]},
            "skill_upgrade_candidates": ["Legacy text must not bypass structured validation"]}


def consume(doc, rows=None):
    return consumer.consume(doc, {"status": "present", "rows": rows or []}, now=NOW)


def history(snapshot):
    return [{"status": "no_necessary_upgrade", "paper_learning_consumption": snapshot}]


def rehash(doc):
    cand = doc["learning_evidence"]["failure_patterns"][0]
    cand["candidate_id"] = consumer.fingerprint({"strategy": cand["strategy"], "evidence": cand["evidence"]})


class PaperLearningConsumerTests(unittest.TestCase):
    def test_research_eligibility_is_independent_of_sizing_and_does_not_patch(self):
        result = consume(packet())
        self.assertEqual(result['status'], 'present')
        self.assertTrue(result['research_upgrade_eligible'])
        self.assertFalse(result['policy_sizing_eligible'])
        self.assertEqual(result['new_review_candidate_count'], 1)
        self.assertEqual(result['new_independent_sample_count'], 10)
        self.assertEqual(result['candidates'][0]['review_state'], 'awaiting_evidence')
        for flag in consumer.BOUNDARY:
            self.assertIs(result[flag], False)
        with patch.object(check, 'latest_learning_packet', return_value={
                'status': 'present', 'materiality_gate': packet()['materiality_gate'],
                'paper_learning_consumption': result, 'skill_upgrade_candidates': []}), \
             patch.object(check, 'trading_memory_review', return_value={}), \
             patch.object(check, 'calibration_snapshot', return_value={}):
            perf = check.performance_snapshot()
        self.assertEqual(perf['performance_materiality'], 'medium')
        self.assertIn('learning_packet:enough_for_trading_research_skill_upgrade_with_new_real_evidence', perf['drivers'])

    def test_same_candidate_and_reordered_sample_set_do_not_repeat_learning(self):
        doc = packet(); first = consume(doc)
        repeat = consume(doc, history(first))
        self.assertEqual(repeat['new_independent_sample_count'], 0)
        self.assertEqual(repeat['new_review_candidate_count'], 0)
        self.assertEqual(repeat['candidates'][0]['novelty'], 'unchanged')
        doc['learning_evidence']['failure_patterns'][0]['evidence']['lifecycle_ids'].reverse(); rehash(doc)
        reorder = consume(doc, history(first))
        self.assertEqual(reorder['new_independent_sample_count'], 0)
        self.assertEqual(reorder['candidates'][0]['novelty'], 'revised_candidate_existing_samples')

    def test_new_oos_or_policy_hash_does_not_create_real_evidence(self):
        doc = packet(); first = consume(doc)
        e = doc['learning_evidence']['failure_patterns'][0]['evidence']
        e.update(replay_as_of='2026-02-01T00:00:00Z', policy_version='new-synthetic-policy')
        e['out_of_sample']['num_trades'] = 999999; rehash(doc)
        self.assertEqual(consume(doc, history(first))['new_independent_sample_count'], 0)

    def test_overlapping_samples_count_only_new_lifecycle_ids(self):
        first = consume(packet(10)); result = consume(packet(11), history(first))
        self.assertEqual(result['new_independent_sample_count'], 1)
        self.assertEqual(result['new_independent_lifecycle_ids'], ['paper:SYNTHETIC.US:fixture-10'])
        self.assertEqual(result['candidates'][0]['real_sample_count'], 11)
        self.assertEqual(result['new_review_candidate_count'], 1)

    def test_duplicate_candidates_in_one_packet_do_not_double_count(self):
        doc = packet(); doc['learning_evidence']['failure_patterns'] *= 2
        result = consume(doc)
        self.assertEqual(result['new_independent_sample_count'], 10)
        self.assertEqual(result['new_review_candidate_count'], 1)
        self.assertEqual(result['candidates'][1]['novelty'], 'unchanged')

    def test_bad_identity_unknown_r_future_or_duplicate_rows_fail_closed(self):
        cases = [lambda d:d['learning_evidence']['failure_patterns'][0].update(candidate_id='fake'),
                 lambda d:d['closed_trade_reviews'][0].update(r_multiple=None),
                 lambda d:d['closed_trade_reviews'][0].update(r_multiple=True),
                 lambda d:d['closed_trade_reviews'][0].update(outcome='unknown'),
                 lambda d:d['closed_trade_reviews'][0].update(completed_at='2999-01-01T00:00:00Z'),
                 lambda d:d['closed_trade_reviews'].append(deepcopy(d['closed_trade_reviews'][0])),
                 lambda d:d['closed_trade_reviews'].append({**deepcopy(d['closed_trade_reviews'][0]), 'r_multiple': None}),
                 lambda d:d['learning_evidence']['failure_patterns'][0].update(auto_apply=True)]
        for mutate in cases:
            doc = packet(); mutate(doc)
            result = consume(doc)
            self.assertEqual(result['new_review_candidate_count'], 0)
            self.assertEqual(result['new_independent_sample_count'], 0)
            self.assertEqual(result['candidates'][0]['validation_status'], 'invalid')

    def test_counterfactual_count_cannot_unlock_research(self):
        doc = packet(2)
        doc['learning_evidence']['failure_patterns'][0]['status'] = 'insufficient_samples'
        result = consume(doc)
        self.assertFalse(result['research_upgrade_eligible'])
        self.assertEqual(result['new_review_candidate_count'], 0)
        self.assertEqual(result['candidates'][0]['reason'], 'insufficient_real_samples_or_research_gate')

    def test_corrupt_history_never_resets_evidence_to_new(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'ledger.jsonl'; path.write_text('{bad}\n')
            result = consumer.consume(packet(), consumer.read_history(path), now=NOW)
            self.assertEqual(result['status'], 'blocked')
            self.assertEqual(result['new_independent_sample_count'], 0)
        bad = consume(packet()); bad['candidates'][0]['real_sample_set_sha256'] = 'fake'
        self.assertEqual(consume(packet(), history(bad))['status'], 'blocked')

    def test_unknown_structured_schema_cannot_fall_back_to_legacy_materiality(self):
        with tempfile.TemporaryDirectory() as directory:
            doc = packet(); doc['learning_evidence']['schema_version'] = 'future.v2'
            path = Path(directory)/'paper_learning_packet_fixture.json'; path.write_text(json.dumps(doc))
            with patch.dict(os.environ, {'PAPER_LEARNING_PACKET_DIR': directory, 'SELF_OPT_LEDGER': str(Path(directory)/'absent.jsonl')}):
                actual = check.latest_learning_packet()
            self.assertTrue(actual['paper_learning_consumption']['structured_evidence_present'])
            with patch.object(check, 'latest_learning_packet', return_value=actual), \
                 patch.object(check, 'trading_memory_review', return_value={}), \
                 patch.object(check, 'calibration_snapshot', return_value={}):
                self.assertEqual(check.performance_snapshot()['performance_materiality'], 'none')
                candidates = generator.learning_packet_candidates()
            self.assertEqual(candidates[0]['reason'], 'structured_learning_evidence_unavailable')
            self.assertNotIn('Legacy text', json.dumps(candidates))

    def test_repeated_structured_samples_do_not_keep_materiality_high(self):
        doc = packet(30); doc['materiality_gate']['enough_for_policy_sizing_change'] = True
        first = consume(doc); repeat = consume(doc, history(first))
        for snapshot, expected in [(first, 'high'), (repeat, 'none')]:
            with patch.object(check, 'latest_learning_packet', return_value={
                    'status': 'present', 'materiality_gate': doc['materiality_gate'],
                    'paper_learning_consumption': snapshot}), \
                 patch.object(check, 'trading_memory_review', return_value={}), \
                 patch.object(check, 'calibration_snapshot', return_value={}):
                self.assertEqual(check.performance_snapshot()['performance_materiality'], expected)

    def test_future_packet_and_unknown_structured_schema_block_sizing_driver(self):
        doc = packet(30); doc['materiality_gate']['enough_for_policy_sizing_change'] = True
        doc['generated_at_utc'] = '2999-01-01T00:00:00Z'
        bad = consume(doc)
        self.assertEqual(bad['status'], 'invalid')
        with patch.object(check, 'latest_learning_packet', return_value={
                'status': 'present', 'materiality_gate': doc['materiality_gate'],
                'paper_learning_consumption': bad}), \
             patch.object(check, 'trading_memory_review', return_value={}), \
             patch.object(check, 'calibration_snapshot', return_value={}):
            self.assertEqual(check.performance_snapshot()['performance_materiality'], 'none')

    def test_concurrent_append_does_not_double_book_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); ledger_path = root/'ledger.jsonl'; check_path = root/'check.json'
            check_path.write_text(json.dumps({'performance_snapshot': {'paper_learning_consumption': consume(packet())}}))
            env = {**os.environ, 'SELF_OPT_LEDGER': str(ledger_path), 'PYTHONDONTWRITEBYTECODE': '1'}
            command = [sys.executable, str(Path(ledger.__file__)), 'append', '--from', str(check_path),
                       '--status', 'no_necessary_upgrade', '--materiality', 'medium']
            processes = [subprocess.Popen(command, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                         for _ in range(2)]
            for process in processes:
                stdout, stderr = process.communicate(timeout=15)
                self.assertEqual(process.returncode, 0, stderr)
            counts = [row['paper_learning_consumption']['new_independent_sample_count'] for row in ledger.read_rows(ledger_path)]
            self.assertEqual(counts, [10, 0])

    def test_reader_triage_ledger_reader_round_trip_is_traceable_and_read_only(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); doc = packet(); packet_path = root/'paper_learning_packet_fixture.json'
            packet_path.write_text(json.dumps(doc)); ledger_path = root/'ledger.jsonl'
            env = {'PAPER_LEARNING_PACKET_DIR': directory, 'SELF_OPT_LEDGER': str(ledger_path)}
            before = packet_path.read_bytes()
            with patch.dict(os.environ, env):
                first = check.latest_learning_packet()
                self.assertFalse(ledger_path.exists())  # Preflight does not persist.
                with patch.object(check, 'latest_learning_packet', return_value=first):
                    triage = generator.learning_packet_candidates()
                self.assertEqual(triage[0]['candidate']['candidate_id'], doc['learning_evidence']['failure_patterns'][0]['candidate_id'])
                check_path = root/'check.json'
                check_path.write_text(json.dumps({'performance_snapshot': {'paper_learning_consumption': first['paper_learning_consumption']}}))
                args = argparse.Namespace(from_check=str(check_path), date='2026-09-05', status='no_necessary_upgrade',
                                          materiality='medium', change=[], changes_json=None, next_watch=[])
                with redirect_stdout(io.StringIO()):
                    ledger.cmd_append(args)
                    ledger.cmd_append(args)  # Exact same stale check JSON cannot count twice.
                persisted = ledger.read_rows(ledger_path)
                self.assertEqual(persisted[0]['paper_learning_consumption']['new_independent_sample_count'], 10)
                self.assertEqual(persisted[1]['paper_learning_consumption']['new_independent_sample_count'], 0)
                second = check.latest_learning_packet()['paper_learning_consumption']
            self.assertEqual(second['new_independent_sample_count'], 0)
            self.assertEqual(second['candidates'][0]['previous_review_state'], 'reviewed_watch')
            self.assertTrue(second['candidates'][0]['previously_triaged'])
            self.assertEqual(packet_path.read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
