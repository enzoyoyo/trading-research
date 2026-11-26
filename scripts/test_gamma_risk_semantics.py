"""Mocked snapshots: no network, production history, or account access."""
import copy
import json
from pathlib import Path
from datetime import datetime, timedelta, timezone
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parent))
import risk_regime_snapshot as r


def modeled(total=100, model_total=100, flip=100, spot=105):
    return {'symbol':'TEST','spot':spot,'put_wall':90,'call_wall':120,
            'source':'cboe_delayed','total_net_gex':total,'regime':'negative_gamma',
            'expirations_used':['2026-09-18'],
            'gamma_flip':flip,'gamma_flip_method':'hypothetical_spot_bs_gamma_v1',
            'gamma_flip_status':'modeled' if flip is not None else 'unknown',
            'dealer_sign_assumption':'assumed_dealer_sign_proxy_call_plus_put_minus',
            'gamma_profile':{'spot_net_gex':model_total,'rate':.045,'dividend_yield':0,
                             'contract_multiplier':100,'iv_assumption':'sticky_strike_frozen_iv','time_basis':'calendar_days/365'}}


class GammaRiskSemanticsTests(unittest.TestCase):
    def build(self, current, previous=None, near=None):
        with patch.object(r,'run_gamma_snapshot',side_effect=[current,near or copy.deepcopy(current)]), patch.object(r,'load_previous_snapshot',return_value=previous):
            return r.build_gex_signal('TEST')

    def test_legacy_history_cannot_confirm_flip_or_outflow(self):
        old=modeled(total=100,flip=120)
        old.pop('gamma_flip_method')
        out,payload,gaps=self.build(modeled(total=40,flip=100),{'aggregate':old})
        self.assertFalse(out['comparable_history'])
        self.assertIsNone(out['metrics']['gamma_flip_shift_pct'])
        self.assertEqual(out['metrics']['confirmed_flags'],[])
        self.assertNotEqual(out['mode'],'confirmed_outflow')
        self.assertTrue(gaps)
        for scope in ('aggregate','near'):
            self.assertEqual(payload[scope]['gamma_flip_method'],'hypothetical_spot_bs_gamma_v1')
            self.assertIn('dealer_sign_assumption',payload[scope])

    def test_same_method_can_compare_but_only_proxy(self):
        old=modeled(total=100,flip=120)
        out,_,_=self.build(modeled(total=40,flip=100),{'aggregate':old,'generated_at':(datetime.now(timezone.utc)-timedelta(minutes=20)).isoformat()})
        self.assertTrue(out['comparable_history'])
        self.assertTrue(out['metrics']['flip_comparable_history'])
        self.assertAlmostEqual(out['metrics']['gamma_flip_shift_pct'],-16.67)
        self.assertEqual(out['mode'],'snapshot_to_snapshot_gex_weakening_proxy')
        self.assertFalse(out['metrics']['observed_dealer_inventory'])

    def test_collection_clock_exposes_intraday_interval_without_invented_daily_baseline(self):
        now='2026-09-04T15:30:00+00:00'
        prior={'aggregate':modeled(total=100,flip=120),'generated_at':'2026-09-04T15:10:00+00:00'}
        with patch.object(r,'iso_now',return_value=now):
            out,_,_=self.build(modeled(total=40,flip=100),prior)
        clock=out['metrics']['comparison_clock']
        self.assertEqual(clock['elapsed_seconds'],1200)
        self.assertEqual(clock['basis'],'intraday_collection_delta')
        self.assertNotEqual(out['mode'],'historical_gex_weakening_proxy')

    def test_unknown_or_future_history_clock_cannot_confirm_change(self):
        for at in (None,'2099-01-01T00:00:00Z','invalid'):
            out,_,gaps=self.build(modeled(total=40,flip=100),{'aggregate':modeled(total=100,flip=120),'generated_at':at})
            self.assertFalse(out['comparable_history'])
            self.assertEqual(out['metrics']['confirmed_flags'],[])
            self.assertTrue(gaps)

    def test_expiry_roll_is_not_same_history_basis(self):
        old=modeled(total=100,flip=120);old['expirations_used']=['2026-09-11']
        out,_,_=self.build(modeled(total=40,flip=100),{'aggregate':old,'generated_at':'2026-09-01T00:00:00Z'})
        self.assertFalse(out['comparable_history'])

    def test_missing_profile_preserves_separate_vendor_sign_but_not_flip(self):
        current=modeled(total=-50,model_total=None,flip=None)
        current['gamma_profile'].update(status='unknown',reason='incomplete_chain_iv_or_expiry_time')
        out,_,gaps=self.build(current)
        self.assertEqual(out['metrics']['aggregate_regime'],'negative_gamma')
        self.assertEqual(out['metrics']['gamma_sign_basis']['aggregate'],'vendor_snapshot_assumed_dealer_sign_proxy')
        self.assertIsNone(out['metrics']['gamma_flip'])
        self.assertIsNone(out['metrics']['aggregate_model_spot_net_gex'])
        self.assertTrue(any('flip remains unknown' in g for g in gaps))

    def test_reverse_root_orientation_uses_model_sign(self):
        out,_,_=self.build(modeled(model_total=-50,spot=105,flip=100))
        self.assertIn('aggregate_negative_gamma',out['metrics']['proxy_flags'])
        out,_,_=self.build(modeled(model_total=50,spot=95,flip=100))
        self.assertEqual(out['metrics']['proxy_flags'],[])
        self.assertFalse(out['triggered'])

    def test_unknown_values_are_not_zero(self):
        out,_,_=self.build(modeled(total=None,model_total=None,flip=None),{'aggregate':modeled()})
        self.assertIsNone(out['metrics']['aggregate_total_net_gex'])
        self.assertIsNone(out['metrics']['aggregate_model_spot_net_gex'])
        self.assertIsNone(out['metrics']['gamma_flip_shift_pct'])
        self.assertEqual(out['metrics']['aggregate_regime'],'unknown')
        self.assertFalse(out['comparable_history'])
        self.assertEqual(out['mode'],'unknown')

    def test_legacy_zero_with_unknown_regime_stays_unknown(self):
        current=modeled(total=0,model_total=None,flip=None)
        current['regime']='unknown'
        current.pop('gamma_flip_method')
        out,payload,_=self.build(current)
        self.assertIsNone(out['metrics']['aggregate_total_net_gex'])
        self.assertIsNone(payload['aggregate']['total_net_gex'])
        self.assertEqual(out['metrics']['aggregate_regime'],'unknown')

    def test_changed_model_assumptions_cannot_compare(self):
        current=modeled(total=20,flip=80)
        current['gamma_profile']['rate']=.09
        out,_,_=self.build(current,{'aggregate':modeled()})
        self.assertFalse(out['comparable_history'])
        self.assertIsNone(out['metrics']['gamma_flip_shift_pct'])

    def test_vendor_fallback_explicitly_labeled(self):
        current=modeled(total=-50)
        current.pop('gamma_flip_method')
        out,_,_=self.build(current)
        self.assertEqual(out['metrics']['gamma_sign_basis']['aggregate'],'vendor_snapshot_assumed_dealer_sign_proxy')
        self.assertNotIn('spot_below_gamma_flip',out['metrics']['proxy_flags'])

    def test_history_signature_includes_method_flip_and_status(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(r,'HISTORY_DIR',Path(tmp)):
            data={'aggregate':modeled(),'near':modeled()}
            path=Path(r.save_snapshot('TEST',data))
            r.save_snapshot('TEST',data)
            self.assertEqual(len(path.read_text().splitlines()),1)
            for key,value in [('gamma_flip',99),('gamma_flip_method','other_v2'),('gamma_flip_status','unknown')]:
                data['aggregate'][key]=value
                r.save_snapshot('TEST',data)
            self.assertEqual(len(path.read_text().splitlines()),4)
            for line in path.read_text().splitlines():
                json.loads(line)

if __name__=='__main__':
    unittest.main()
