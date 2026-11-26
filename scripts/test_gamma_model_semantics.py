"""Synthetic math tests; no operator cache access or live data."""
import math
from pathlib import Path
import unittest
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
import options_gamma as g


class SyntheticGammaMathTests(unittest.TestCase):
    def test_analytic_two_strike_root(self):
        # Equal OI, IV, expiry: equal BS gammas at geometric mean discounted by d1 drift.
        t, iv, r = 30/365, 0.3, 0.045
        contracts = [g.GammaContract(90, 100, iv, t, -1), g.GammaContract(110, 100, iv, t, 1)]
        out = g._spot_gamma_profile(contracts, 100, r)
        expected = math.sqrt(90*110)*math.exp(-(r+iv*iv/2)*t)
        self.assertEqual(out['status'], 'modeled')
        self.assertAlmostEqual(out['gamma_flip'], expected, places=8)
        self.assertNotAlmostEqual(out['gamma_flip'], 100, places=2)

    def test_one_sign_and_exact_cancellation_have_no_flip(self):
        c = g.GammaContract(100,100,.3,.1,1)
        for chain in ([c], [c,g.GammaContract(100,100,.3,.1,-1)]):
            out = g._spot_gamma_profile(chain,100,.045)
            self.assertIsNone(out['gamma_flip'])
            self.assertEqual(out['status'],'unknown')

    def test_missing_iv_zero_dte_nan_fail_closed(self):
        for iv, t in [(0,.1), (.3,0), (float('nan'),.1)]:
            out=g._spot_gamma_profile([g.GammaContract(100,100,iv,t,1)],100,.045)
            self.assertIsNone(out['gamma_flip'])
            self.assertEqual(out['reason'],'incomplete_chain_iv_or_expiry_time')

    def test_actual_sign_can_be_reverse_of_price_above_flip(self):
        chain=[g.GammaContract(90,100,.3,.1,1),g.GammaContract(110,100,.3,.1,-1)]
        out=g._spot_gamma_profile(chain,105,.045)
        self.assertGreater(105,out['gamma_flip'])
        self.assertLess(out['spot_net_gex'],0)

    def test_legacy_is_separately_named_in_output(self):
        s=g.GammaStructure('TEST','synthetic','synthetic',100,'synthetic',[],0,'unknown',None,None,None,[],[])
        data=g.asdict(s)
        self.assertIn('legacy_strike_cumulative_crossing',data)
        self.assertFalse(data['observed_dealer_inventory'])
        self.assertFalse(data['direction_authority'])
        self.assertFalse(data['position_authority'])
        self.assertNotIn('回踩 Put Wall 接',g._render_text(s))

    def test_legacy_cumulative_is_not_spot_root(self):
        result=g._find_walls([g.StrikeGex(90,0,100,-1),g.StrikeGex(110,100,0,2)],100)
        self.assertEqual(result[2],100)
        out=g._spot_gamma_profile([],100,.045)
        self.assertIsNone(out['gamma_flip'])



if __name__=='__main__':
    unittest.main()
