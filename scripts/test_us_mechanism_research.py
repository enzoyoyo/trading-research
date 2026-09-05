"""Synthetic integration fixtures, never genuine trades or historical forecasts."""
import copy
import unittest
from unittest.mock import patch

from us_mechanism_research import assemble, feedback
from test_event_dislocation import fixture as event_fixture
from test_conditional_path_study import fixture as path_fixture
from test_options_expression_lab import fixture as option_fixture
from test_honest_paper_calibration import entry, close


def request():
    return {"schema_version": "us_mechanism_request.v1", "research_id": "synthetic-integration",
            "as_of": "2026-09-05T12:00:00Z", "scope_symbols": ["TARGET", "ISSUER", "BENCH", "SYNTHETIC", "TEST", "ABC"],
            "horizon_id": "swing_days", "hypothesis_ids": ["synthetic"], "invalidation": ["synthetic scenario breaks relationship"],
            "components": {"event_dislocation": event_fixture(), "conditional_paths": path_fixture(), "options_expression": option_fixture()}}


def linked_fixture():
    bundle = assemble(request())
    bundle["receipt_recorded_at"] = "2026-09-05T12:01:00Z"
    opening = entry()
    opening.update(symbol="TARGET.US", timestamp_utc="2026-09-05T13:00:00Z", mechanism_research_link=copy.deepcopy(bundle["research_link"]), horizon_id="swing_days")
    opening["paper_prediction_contract"]["symbol"] = "TARGET.US"
    closing = close(); closing.update(symbol="TARGET.US", timestamp_utc="2026-09-06T13:00:00Z")
    return bundle, opening, closing


class MechanismResearchTests(unittest.TestCase):
    def test_all_calculators_run_without_blending_probability_or_permission(self):
        result = assemble(request())
        self.assertEqual(result["state"], "research_computed")
        self.assertEqual(len(result["computed_components"]), 3)
        self.assertIsNone(result["probability_profit"])
        self.assertIsNone(result["expected_net_return"])
        self.assertFalse(result["action_authority"])
        self.assertIn("model", result["components"]["event_dislocation"]["output"])
        self.assertIn("empirical_path_cone", result["components"]["conditional_paths"]["output"])
        self.assertIn("expiry_geometry_per_requested_units", result["components"]["options_expression"]["output"]["candidates"][0])
        self.assertNotIn("ISSUER", result["attribution_symbols"])
        self.assertNotIn("BENCH", result["attribution_symbols"])
        self.assertIn("TARGET", result["attribution_symbols"])

    def test_broken_option_branch_preserves_event_and_paths(self):
        data = request(); del data["components"]["options_expression"]["candidates"][0]["costs"]
        result = assemble(data)
        self.assertEqual(result["state"], "research_computed")
        self.assertNotIn("options_expression", result["computed_components"])
        self.assertIn("event_dislocation", result["computed_components"])

    def test_scope_future_and_wrong_horizon_fail_closed_per_component(self):
        for mutation, name in (("scope", "event_dislocation"), ("future", "options_expression"), ("horizon", "conditional_paths")):
            data = request()
            if mutation == "scope": data["scope_symbols"].remove("TARGET")
            if mutation == "future": data["components"][name]["as_of"] = "2099-01-01T00:00:00Z"
            if mutation == "horizon": data["horizon_id"] = "overnight_cto"
            result = assemble(data)
            self.assertNotIn(name, result["computed_components"])
            self.assertEqual(result["components"][name]["state"], "blocked")

    def test_code_changes_and_inputs_have_distinct_frozen_contracts(self):
        data = request(); first = assemble(data)
        with patch("us_mechanism_research.calculator_fingerprint", return_value="different-calculator"):
            second = assemble(data)
        self.assertEqual(first["request_sha256"], second["request_sha256"])
        self.assertNotEqual(first["contract_sha256"], second["contract_sha256"])
        data["invalidation"].append("another observed invalidation")
        self.assertNotEqual(first["request_sha256"], assemble(data)["request_sha256"])

    def test_unknown_component_not_silently_dropped(self):
        data = request(); data["components"]["magic_win_rate"] = {}
        self.assertEqual(assemble(data)["state"], "blocked")

    def test_one_daily_bar_is_still_not_close_to_open(self):
        data = request(); data["horizon_id"] = "overnight_cto"
        data["components"]["conditional_paths"]["rule"]["horizon"] = 1
        self.assertNotIn("conditional_paths", assemble(data)["computed_components"])

    def test_nested_gaps_are_visible_at_the_bundle_level(self):
        data = request(); data["components"]["event_dislocation"]["candidate"]["relationship"] = {}
        result = assemble(data)
        self.assertTrue(any("event_dislocation:" in g for g in result["data_gaps"]))
        self.assertTrue(any("conditional_paths:" in g for g in result["data_gaps"]))

    def test_entry_link_plus_actual_net_receipt_deduplicates_lifecycle(self):
        b, e, c = linked_fixture()
        result = feedback([b], [e, e, c])
        self.assertEqual((result["linked_entries"], result["completed_linked"], result["net_settled_linked"]), (1, 1, 1))
        self.assertEqual(result["rows"][0]["net_pnl"], -2)
        self.assertFalse(result["materiality_eligible"])

    def test_late_artifact_or_changed_horizon_cannot_receive_old_profit(self):
        for kind in ("late", "wrong_horizon", "wrong_hash", "missing_clock"):
            b, e, c = linked_fixture()
            if kind == "late": b["receipt_recorded_at"] = "2026-09-07T00:00:00Z"
            if kind == "wrong_horizon": e["horizon_id"] = "intraday"
            if kind == "wrong_hash": e["mechanism_research_link"]["contract_sha256"] = "bad"
            if kind == "missing_clock": b.pop("receipt_recorded_at")
            result = feedback([b], [e, c])
            self.assertEqual(result["linked_entries"], 0)
            self.assertEqual(result["invalid_links"], 1)

    def test_missing_cost_stays_pending_and_old_trades_unlinked(self):
        b, e, c = linked_fixture(); c.pop("cost_evidence_refs")
        result = feedback([b], [e, c])
        self.assertEqual(result["completed_linked"], 1)
        self.assertEqual(result["net_settled_linked"], 0)
        self.assertIsNone(result["rows"][0]["net_pnl"])
        del e["mechanism_research_link"]
        self.assertEqual(feedback([b], [e, c])["legacy_unlinked"], 1)

    def test_input_only_issuer_and_benchmark_cannot_receive_trade_attribution(self):
        for asset in ('ISSUER', 'BENCH'):
            b, e, c = linked_fixture()
            e['symbol']=c['symbol']=asset+'.US'
            result=feedback([b],[e,c])
            self.assertEqual(result['linked_entries'],0)
            self.assertEqual(result['rows'][0]['gap'],'entry_symbol_is_input_only_not_research_subject')

    def test_blocked_component_cannot_expand_attribution_scope(self):
        data=request();del data['components']['options_expression']['candidates'][0]['costs']
        result=assemble(data)
        self.assertNotIn('options_expression',result['attribution_by_component'])
        self.assertNotIn('ABC',result['attribution_symbols'])

    def test_digest_metadata_and_old_unqualified_scope_are_rejected(self):
        for kind in ('digest','old_scope'):
            b,e,c=linked_fixture()
            if kind=='digest': b['calculator_sha256']='0'*64
            else:
                b.pop('attribution_symbols');b['research_link'].pop('attribution_symbols')
                e['mechanism_research_link']=copy.deepcopy(b['research_link'])
            result=feedback([b],[e,c])
            self.assertEqual(result['linked_entries'],0)
            self.assertEqual(result['invalid_links'],1)


if __name__ == "__main__":
    unittest.main()
