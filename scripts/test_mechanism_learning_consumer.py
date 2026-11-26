"""Independent mechanism observations must survive without changing eligibility."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import self_optimization_check as consumer


class MechanismLearningConsumerTests(unittest.TestCase):
    def read_packet(self, extra):
        with tempfile.TemporaryDirectory() as directory:
            doc = {'schema_version': 'paper_learning_packet.v2', 'summary': {'closed_trades': 0},
                   'materiality_gate': {'enough_for_policy_sizing_change': False},
                   'skill_upgrade_candidates': [], **extra}
            Path(directory, 'paper_learning_packet_test.json').write_text(json.dumps(doc))
            with patch.dict(os.environ, {'PAPER_LEARNING_PACKET_DIR': directory}):
                return consumer.latest_learning_packet()

    def test_valid_block_is_preserved_independently(self):
        block = {'schema_version': 'paper_mechanism_learning.v1', 'state': 'computed',
                 'computed_count': 3, 'stale_count': 2, 'feedback': {'linked_entries': 0},
                 'research_receipts': [{'research_id': 'synthetic'}],
                 'data_gaps': ['synthetic_gap'], 'action_authority': False,
                 'materiality_eligible': False, 'changes_existing_learning_gates': False}
        result = self.read_packet({'mechanism_research': block})
        self.assertEqual(result['mechanism_research'], block)
        self.assertEqual(result['status'], 'present')

    def test_old_packet_and_invalid_block_remain_explicit_unknown(self):
        for extra in ({}, {'mechanism_research': []}, {'mechanism_research': {'schema_version': 'future.v2', 'computed_count': 999}}):
            with self.subTest(extra=extra):
                result = self.read_packet(extra)
                self.assertEqual(result['status'], 'present')
                self.assertEqual(result['summary'], {'closed_trades': 0})
                block = result['mechanism_research']
                self.assertEqual(block['state'], 'unknown')
                self.assertIsNone(block['feedback'])
                self.assertTrue(block['data_gaps'])
                self.assertNotIn('computed_count', block)

    def test_counts_and_claimed_net_success_cannot_raise_materiality(self):
        block = {'schema_version': 'paper_mechanism_learning.v1', 'computed_count': 999999,
                 'feedback': {'net_settled_linked': 999999, 'net_pnl': 1e12},
                 'action_authority': True, 'materiality_eligible': True,
                 'changes_existing_learning_gates': True}
        packet = self.read_packet({'mechanism_research': block})
        with patch.object(consumer, 'latest_learning_packet', return_value=packet), \
             patch.object(consumer, 'trading_memory_review', return_value={'sample_count': 0}), \
             patch.object(consumer, 'calibration_snapshot', return_value={'calibration_materiality': 'none'}):
            result = consumer.performance_snapshot()
        self.assertEqual(result['performance_materiality'], 'none')
        self.assertEqual(result['drivers'], [])
        retained = result['learning_packet']['mechanism_research']
        self.assertEqual(retained['feedback']['net_settled_linked'], 999999)
        for flag in ('action_authority', 'materiality_eligible', 'changes_existing_learning_gates'):
            self.assertIs(retained[flag], False)


if __name__ == '__main__':
    unittest.main()
