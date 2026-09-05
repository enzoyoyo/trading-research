"""Synthetic fixtures; not actual quotes, forecast probabilities or trade permission."""
import copy
from decimal import Decimal
import unittest
from options_expression_lab import analyze, bs_european


def fixture():
    legs=[]
    for strike,qty,bid,ask in [(90,1,11,12),(100,-2,5,6),(110,1,1,2)]:
        legs.append({'contract_id':f'TEST261016C{strike*1000:08d}','underlying':'TEST','expiry':'2026-10-16T20:00:00Z',
            'last_trade_at':'2026-10-16T20:00:00Z','right':'call','strike':strike,'signed_qty':qty,'multiplier':100,
            'currency':'USD','exercise_style':'european','settlement_style':'cash','settlement_session':'PM',
            'standard_deliverable_verified':True,'identity_evidence_ref':'synthetic_identity',
            'quote_source':'synthetic_fixture','quote_asof':'2026-09-05T12:00:00Z',
            'bid':bid,'ask':ask,'bid_size':10,'ask_size':10})
    return {'as_of':'2026-09-05T12:00:00Z','data_mode':'synthetic',
        'policy':{'max_quote_age_seconds':60,'max_quote_skew_seconds':2,'max_spread_fraction':0.75},
        'candidates':[{'id':'fly','kind':'butterfly','units':1,'legs':legs,
                       'costs':{'entry_total':4,'expiry_total':0,'model_exit_total':4,'evidence_ref':'synthetic_explicit_cost_assumptions'}}],
        'scenarios':[{'id':str(s),'at':'2026-10-16T20:00:00Z','prices':{'TEST':s}} for s in (70,100,130)]}


class OptionsExpressionTests(unittest.TestCase):
    def candidate(self,p): return analyze(p)['candidates'][0]
    def assertBlocked(self,p,text):
        c=self.candidate(p);self.assertEqual(c['readiness'],'blocked');self.assertTrue(any(text in g for g in c['data_gaps']),c)
    def test_quote_clock_receipt_uses_contract_quotes(self):
        data=fixture();out=analyze(data)
        clocks=out['source_freshness']['clocks']
        self.assertEqual(len(clocks),3)
        self.assertTrue(all(c['kind']=='quote_asof' and c['at']==data['candidates'][0]['legs'][0]['quote_asof'] for c in clocks))
        self.assertTrue(all(c['max_age_seconds']==60 for c in clocks))

    def test_fly_profit_at_body_loss_on_breakout_exact_geometry(self):
        c=self.candidate(fixture())
        self.assertEqual(c['readiness'],'hypothesis_only')
        self.assertEqual([Decimal(s['option_pnl']) for s in c['scenarios']],[-404,596,-404])
        g=c['expiry_geometry_per_requested_units']
        self.assertEqual(g['max_loss'],'404');self.assertEqual(g['max_profit'],'596')
        self.assertEqual(g['breakeven_prices'],['94.04','105.96'])
        self.assertIsNone(c['expected_pnl']);self.assertIsNone(c['probability_profit'])
    def test_natural_not_mid_and_ratio_capacity(self):
        c=self.candidate(fixture())
        self.assertEqual(c['natural_entry_debit'],'400') # mid would incorrectly be 200
        self.assertEqual(c['displayed_capacity_units'],5)
        p=fixture();p['candidates'][0]['units']=6;self.assertBlocked(p,'capacity')
    def test_incomplete_quote_fee_identity_and_stale_fail_closed(self):
        for key in ('bid','ask_size','quote_asof','multiplier','identity_evidence_ref'):
            p=fixture();del p['candidates'][0]['legs'][0][key];self.assertBlocked(p,key)
        p=fixture();del p['candidates'][0]['costs']['entry_total'];self.assertBlocked(p,'entry_total')
        p=fixture();p['candidates'][0]['legs'][0]['quote_asof']='2026-09-04T12:00:00Z';self.assertBlocked(p,'stale')
    def test_asymmetric_butterfly_wrong_expiry_multiplier_and_ratio(self):
        for key,val,expected in [('strike',111,'structure'),('expiry','2026-10-17T20:00:00Z','mixed_expiry'),
                                 ('multiplier',10,'mixed_multiplier'),('signed_qty',2,'structure')]:
            p=fixture();p['candidates'][0]['legs'][2][key]=val;self.assertBlocked(p,expected)
    def test_preexpiry_is_not_expiry_even_one_day_to_expiry(self):
        p=fixture();p['scenarios'][0]['at']='2026-10-15T20:00:00Z';self.assertBlocked(p,'pre_expiry:model_missing')
    def test_rise_in_underlying_can_lose_after_iv_crush(self):
        p=fixture();c=p['candidates'][0];c.update(kind='single');c['legs']=[c['legs'][1]]
        l=c['legs'][0];l['signed_qty']=1
        t=(41+8/24)/365;mark=bs_european(100,100,t,1,0.04,0,'call')['price']
        l['bid']=mark-0.1;l['ask']=mark+0.1
        p['scenarios']=[{'id':'up_but_iv_crush','at':'2026-09-06T12:00:00Z','prices':{'TEST':102},
            'model':{'name':'black_scholes_european','rate':0.04,'dividend_yield':0,
                     'iv_by_contract':{l['contract_id']:0.15},'assumptions':'synthetic constant rate continuous yield',
                     'input_evidence_ref':'synthetic_hypothetical_IV_not_prediction'}}]
        out=self.candidate(p)
        self.assertEqual(out['readiness'],'hypothesis_only',out)
        self.assertLess(Decimal(out['scenarios'][0]['option_pnl']),0)
        self.assertIn('model_mark',out['scenarios'][0]['valuation'])
        self.assertGreater(out['scenarios'][0]['remaining_calendar_years_ACT365'],40/365)
        self.assertIn('vega_per_unit_iv',out['scenarios'][0]['marks_and_greeks_per_option_unit'][0])
    def test_bs_put_call_parity(self):
        import math
        call=bs_european(100,105,0.5,0.25,0.04,0.01,'call')['price']
        put=bs_european(100,105,0.5,0.25,0.04,0.01,'put')['price']
        self.assertAlmostEqual(call-put,100*math.exp(-.01*.5)-105*math.exp(-.04*.5))
    def test_single_and_vertical_unbounded_tail(self):
        p=fixture();c=p['candidates'][0];c['kind']='single';c['legs']=c['legs'][:1]
        self.assertEqual(self.candidate(p)['expiry_geometry_per_requested_units']['max_profit'],'unbounded')
        c['legs'][0]['signed_qty']=-1
        self.assertEqual(self.candidate(p)['expiry_geometry_per_requested_units']['max_loss'],'unbounded')
        p=fixture();c=p['candidates'][0];c['kind']='vertical';c['legs']=c['legs'][:2];c['legs'][1]['signed_qty']=-1
        self.assertEqual(self.candidate(p)['readiness'],'hypothesis_only')
    def test_weights_require_qualification_and_not_equal_grid(self):
        p=fixture()
        for s,w in zip(p['scenarios'],['0.1','0.8','0.1']):s['weight']=w
        self.assertIsNone(self.candidate(p)['expected_pnl'])
        p['weight_qualification']={'status':'qualified','measure':'physical','validator':'synthetic_validator',
            'method':'synthetic_example','validation_evidence_ref':'synthetic_fixture', 'limitations':'not empirically validated',
            'frozen_at':p['as_of'],'valid_until':'2026-09-06T12:00:00Z','horizon_at':p['scenarios'][0]['at'],
            'scenario_ids':[s['id'] for s in p['scenarios']]}
        self.assertEqual(Decimal(self.candidate(p)['expected_pnl']),Decimal('396'))
        p['weight_qualification']['measure']='risk_neutral';self.assertIsNone(self.candidate(p)['expected_pnl'])
    def test_hedge_evidence_and_joint_stress_pareto(self):
        p=fixture();p['holdings']=[{'kind':'stock','symbol':'STOCK','currency':'USD','signed_shares':100,
            'reference_price':100,'as_of':p['as_of'],'exposure_evidence_ref':'synthetic_holding'}]
        for s,spot,tag in zip(p['scenarios'],[60,110,80],['tail','basis_break','correlation_failure']):
            s['prices']['STOCK']=spot;s['stress_tags']=[tag]
        self.assertNotIn('hedge_metrics',self.candidate(p))
        p['related_exposure_evidence_ref']='synthetic_joint_scenario_assumption'
        o=analyze(p);c=o['candidates'][0]
        self.assertEqual(c['hedge_metrics']['worst_loss_reduction_on_given_scenarios'],'-404')
        self.assertEqual(o['pareto']['candidate_ids'],['fly']) # nondominated does NOT mean a useful hedge
        self.assertEqual(o['hedge_claim'],'not_established')
        self.assertTrue(o['no_order_execution'])
    def test_spx_style_and_last_trading_time_gate(self):
        p=fixture()
        for l in p['candidates'][0]['legs']:l['underlying']='SPX';l['option_root']='SPX'
        self.assertBlocked(p,'SPX:')
        p=fixture();p['candidates'][0]['legs'][0]['last_trade_at']=p['as_of'];self.assertBlocked(p,'no_longer_trading')
    def test_id_terms_mismatch_and_future_qualification(self):
        p=fixture();p['candidates'][0]['legs'][0]['contract_id']='WRONG261016C00090000';self.assertBlocked(p,'identity:')
        p=fixture();p['scenarios'][0]['at']='2026-10-17T20:00:00Z';self.assertBlocked(p,'after_settlement')
    def option_hedge_fixture(self):
        p=fixture()
        for leg in p['candidates'][0]['legs']:
            leg['underlying']='SPX';leg['option_root']='SPXW'
            leg['contract_id']=leg['contract_id'].replace('TEST','SPXW')
        holding={'kind':'option','contract_id':'STOCK261120C00100000','underlying':'STOCK',
            'expiry':'2026-11-20T21:00:00Z','last_trade_at':'2026-11-20T21:00:00Z','right':'call','strike':100,'signed_qty':1,'multiplier':100,
            'currency':'USD','exercise_style':'american','settlement_style':'physical','settlement_session':'PM',
            'identity_evidence_ref':'synthetic_identity','standard_deliverable_verified':True,
            'reference_mark':20,'reference_mark_asof':p['as_of'],'reference_mark_evidence_ref':'synthetic_current_mark',
            'as_of':p['as_of'],'exposure_evidence_ref':'synthetic_option_holding',
            'historical_entry_cost':99999,'costs':{'model_exit_total':0,'expiry_total':0,'evidence_ref':'synthetic_fee_assumption'}}
        p['holdings']=[holding];p['related_exposure_evidence_ref']='synthetic_cross_asset_stress_not_estimated_correlation'
        for scenario,spot,mark,tag in zip(p['scenarios'],[80,102,85],[1,12,2],['tail','basis_break','correlation_failure']):
            scenario['prices']['SPX']=scenario['prices'].pop('TEST');scenario['prices']['STOCK']=spot
            scenario['stress_tags']=[tag]
            scenario['marks_by_contract']={holding['contract_id']:{'mark':mark,'at':scenario['at'],'expiry':holding['expiry'],
                'underlying_price':spot,'iv':0.15,'mode':'assumption','assumptions':'synthetic American option IV crush mark; not observed',
                'evidence_ref':'synthetic_scenario_fixture'}}
        return p

    def test_american_stock_call_plus_spx_butterfly_can_increase_tail_loss(self):
        p=self.option_hedge_fixture();result=analyze(p);c=result['candidates'][0]
        self.assertEqual(c['readiness'],'hypothesis_only',result)
        self.assertEqual(c['hedge_metrics']['worst_loss_reduction_on_given_scenarios'],'-404')
        self.assertEqual(c['scenarios'][0]['unhedged_pnl'],'-1900')
        self.assertEqual(c['scenarios'][0]['hedged_pnl'],'-2304')
        self.assertIsNone(c['expected_pnl'])
        self.assertEqual(result['baseline_scenario_details'][0]['holdings'][0]['valuation'],'explicit_scenario_mark_assumption_not_observed')

    def test_european_holding_uses_own_remaining_expiry_and_iv(self):
        p=self.option_hedge_fixture();h=p['holdings'][0];h['exercise_style']='european'
        for scenario in p['scenarios']:
            scenario['at']='2026-10-15T20:00:00Z';scenario.pop('marks_by_contract')
            scenario['model']={'name':'black_scholes_european','rate':0.04,'dividend_yield':0,
                'by_underlying':{'STOCK':{'rate':0.04,'dividend_yield':0.01},'SPX':{'rate':0.04,'dividend_yield':0}},
                'iv_by_contract':{leg['contract_id']:.2 for leg in p['candidates'][0]['legs']},
                'assumptions':'synthetic distinct maturities and IVs','input_evidence_ref':'synthetic'}
            scenario['model']['iv_by_contract'][h['contract_id']]=.4
        r=analyze(p);c=r['candidates'][0]
        self.assertIn('hedge_metrics',c,r)
        holding=r['baseline_scenario_details'][0]['holdings'][0]
        self.assertGreater(holding['remaining_calendar_years_ACT365'],30/365)
        self.assertAlmostEqual(c['scenarios'][0]['remaining_calendar_years_ACT365'],1/365)
        self.assertEqual(holding['iv'],'0.4')

    def test_partial_asset_model_cannot_silently_use_other_asset_terms(self):
        from options_expression_lab import valuation_terms, Gap
        model={'rate':.04,'dividend_yield':.01,'by_underlying':{'STOCK':{'rate':.04,'dividend_yield':.02}}}
        self.assertEqual(valuation_terms(model,'STOCK')['dividend_yield'],.02)
        with self.assertRaisesRegex(Gap,'missing_terms_for:SPX'):
            valuation_terms(model,'SPX')
        p=fixture()
        for scenario in p['scenarios']:
            scenario['at']='2026-10-15T20:00:00Z'
            scenario['model']={'name':'black_scholes_european',**model,
                'iv_by_contract':{leg['contract_id']:.2 for leg in p['candidates'][0]['legs']},
                'assumptions':'synthetic per-asset terms missing TEST','input_evidence_ref':'synthetic'}
        result=analyze(p)
        self.assertEqual(result['candidates'][0]['readiness'],'blocked')
        self.assertTrue(any('missing_terms_for:TEST' in s for s in result['candidates'][0]['data_gaps']))

    def test_previous_entry_cost_never_double_counted(self):
        p=self.option_hedge_fixture();a=analyze(p)
        p['holdings'][0]['historical_entry_cost']=1;b=analyze(p)
        self.assertEqual(a['baseline_scenario_details'],b['baseline_scenario_details'])
        self.assertEqual(a['candidates'][0]['scenarios'],b['candidates'][0]['scenarios'])

    def test_hedge_time_currency_missing_iv_block_only_hedge(self):
        for mode in ('time','iv','currency','observed'):
            p=self.option_hedge_fixture();mark=next(iter(p['scenarios'][0]['marks_by_contract'].values()))
            if mode=='time':mark['at']='2026-10-15T20:00:00Z'
            elif mode=='iv':del mark['iv']
            elif mode=='currency':p['holdings'][0]['currency']='HKD'
            else:mark['mode']='observed'
            c=analyze(p)['candidates'][0]
            self.assertEqual(c['readiness'],'hypothesis_only',(mode,c))
            self.assertNotIn('hedge_metrics',c)
            self.assertTrue(c['hedge_data_gaps'],mode)
        p=self.option_hedge_fixture();r=analyze(p)
        # Underlying rises to 102 but option mark drops from 20 to 12 under IV crush.
        self.assertEqual(r['candidates'][0]['scenarios'][1]['unhedged_pnl'],'-800')

    def test_different_expiry_requires_explicit_cash_and_physical_delivery_policy(self):
        p=self.option_hedge_fixture();h=p['holdings'][0]
        h['expiry']='2026-10-09T20:00:00Z';h['last_trade_at']=h['expiry'];h['contract_id']='STOCK261009C00100000'
        c=analyze(p)['candidates'][0]
        self.assertEqual(c['readiness'],'hypothesis_only');self.assertNotIn('hedge_metrics',c)
        for scenario in p['scenarios']:
            scenario['settlements_by_contract']={h['contract_id']:{'at':h['expiry'],'underlying_price':105,
                'cash_policy':'settle_to_cash_and_carry','cash_carry_rate':0,
                'physical_delivery_policy':'immediate_cash_equivalent_liquidation',
                'assumptions':'synthetic all exercise and liquidation costs in expiry_total; no residual stock',
                'evidence_ref':'synthetic_settlement_assumption'}}
        c=analyze(p)['candidates'][0]
        self.assertIn('hedge_metrics',c)
        self.assertEqual(c['scenarios'][0]['unhedged_pnl'],'-1500.0')

    def test_nan_crossed_and_fractional_sizes(self):
        for key,val,error in [('bid','NaN','bid'),('bid',20,'crossed'),('ask_size',1.5,'integer')]:
            p=fixture();p['candidates'][0]['legs'][0][key]=val;self.assertBlocked(p,error)


if __name__=='__main__':unittest.main()
