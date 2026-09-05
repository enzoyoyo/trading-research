"""Synthetic causal-path stress tests; no historical predictions are manufactured."""
import copy
from datetime import date, timedelta
import math
import unittest
from event_dislocation import analyze, partial_fit


def fixture(common_only=False):
    dates=[];d=date(2026,1,2)
    while len(dates)<85:
        if d.weekday()<5:dates.append(d.isoformat())
        d+=timedelta(days=1)
    b=[0.003*math.sin(j*1.3)+0.001*math.cos(j*.7) for j in range(80)]
    i=[1.4*x+0.007*math.sin(j*.81) for j,x in enumerate(b)]
    noise=[0.002*math.sin(j*2.11)+0.001*math.cos(j*.43) for j in range(80)]
    e=partial_fit(b,i,noise)['residuals']
    t=[(3*x if common_only else .0002+.8*x+.6*y)+z for x,y,z in zip(b,i,e)]
    if common_only:
        br=[.03,.02,.01,-.005];ir=[1.4*x for x in br];tr=[3*x for x in br]
    else:
        br=[.001,-.002,.003,.001];ir=[.10,0,0,0];tr=[-.02,0,0,0]
    series={}
    asof=dates[-1]+'T22:00:00Z'
    for symbol,rets in [('BENCH',b+br),('ISSUER',i+ir),('TARGET',t+tr)]:
        px=100;rows=[{'date':dates[0],'close':px,'close_at':dates[0]+'T20:00:00Z'}]
        for ds,r in zip(dates[1:],rets):
            px*=math.exp(r);rows.append({'date':ds,'close':px,'close_at':ds+'T20:00:00Z'})
        series[symbol]={'source_ref':'synthetic://daily','retrieved_at':asof,'price_basis':'synthetic_adjusted','rows':rows}
    published=dates[81]+'T10:45:00Z'
    return {'as_of':asof,'retrospective_only':False,'benchmark':'BENCH',
        'event':{'event_id':'synthetic_event','published_at':published,'known_at':published,'source_ref':'synthetic://event','issuer':'ISSUER'},
        'candidate':{'symbol':'TARGET','relationship':{'kind':'co_development','description':'synthetic joint product',
            'source_ref':'synthetic://relationship','known_at':published,'issuer':'ISSUER','target':'TARGET'}},
        'rule':{'rule_id':'synthetic_frozen_rule','declared_at':dates[0]+'T10:00:00Z','lookback':80,
            'min_abs_residual_z':2,'max_event_sessions':10,'max_data_age_hours':72},'series':series}


class EventDislocationTests(unittest.TestCase):
    def assertBlocked(self,p,part):
        r=analyze(p);self.assertEqual(r['readiness'],'blocked',r)
        self.assertTrue(any(part in x for x in r['data_gaps']),r)
    def test_frozen_three_variable_fit_and_nonoverlap_blocks(self):
        r=analyze(fixture());self.assertEqual(r['readiness'],'hypothesis_only',r)
        self.assertAlmostEqual(r['model']['beta_benchmark'],.8,places=10)
        self.assertAlmostEqual(r['model']['beta_issuer'],.6,places=10)
        self.assertAlmostEqual(r['model']['alpha_per_session'],.0002,places=10)
        self.assertEqual(r['model']['training_n'],80)
        self.assertEqual(r['event_response']['event_sessions'],4)
        self.assertEqual(r['descriptive_distribution']['block_count'],20)
        self.assertEqual(r['classification'],'relative_dislocation_candidate')
        self.assertEqual(r['event_response']['relative_side'],'laggard')
        self.assertIsNone(r['reversion_probability']);self.assertIsNone(r['price_target']);self.assertIsNone(r['net_edge'])
    def test_forward_estimators_explicitly_unimplemented_even_with_extra_data(self):
        p=fixture();p['matched_event_paths']=[{'synthetic':True}]
        for r in (analyze(p), analyze({})):
            self.assertEqual(r['forward_estimation_capability']['status'],'not_implemented')
            self.assertFalse(r['forward_estimation_capability']['additional_data_alone_enables_estimation'])
            self.assertIn('forward_estimates:estimator_not_implemented',r['data_gaps'])
            for field in r['forward_estimation_capability']['fields']:
                self.assertIsNone(r[field])

    def test_post_event_shock_does_not_leak_into_betas(self):
        p=fixture();before=analyze(p)
        for symbol in p['series']:
            for row in p['series'][symbol]['rows'][81:]:row['close']*=4 if symbol=='TARGET' else .4
        after=analyze(p)
        self.assertEqual(before['model'],after['model'])
        self.assertNotEqual(before['event_response']['extra_residual_logreturn'],after['event_response']['extra_residual_logreturn'])
    def test_common_benchmark_move_removed(self):
        r=analyze(fixture(common_only=True))
        self.assertAlmostEqual(r['model']['beta_issuer'],0,places=10)
        self.assertAlmostEqual(r['model']['beta_benchmark'],3,places=10)
        self.assertAlmostEqual(r['event_response']['extra_residual_logreturn'],0,places=10)
        self.assertEqual(r['classification'],'no_threshold_dislocation')
    def test_singular_benchmark_and_issuer_block(self):
        p=fixture()
        for row in p['series']['BENCH']['rows']:row['close']=100
        self.assertBlocked(p,'singular_benchmark')
        p=fixture();p['series']['ISSUER']['rows']=copy.deepcopy(p['series']['BENCH']['rows'])
        self.assertBlocked(p,'singular_issuer')
    def test_future_duplicate_missing_and_bad_price_block(self):
        p=fixture();p['series']['TARGET']['rows'][-1]['close_at']='2099-01-01T20:00:00Z';p['series']['TARGET']['rows'][-1]['date']='2099-01-01';self.assertBlocked(p,'future_close')
        p=fixture();p['series']['TARGET']['rows'].append(copy.deepcopy(p['series']['TARGET']['rows'][-1]));self.assertBlocked(p,'duplicate')
        p=fixture();p['series']['TARGET']['rows'].pop(20);self.assertBlocked(p,'unaligned_session')
        p=fixture();p['series']['TARGET']['rows'][20]['close']=0;self.assertBlocked(p,'nonpositive')
    def test_wrong_relationship_keeps_stats_but_cannot_claim_dislocation(self):
        p=fixture();p['candidate']['relationship']['target']='OTHER'
        r=analyze(p);self.assertEqual(r['readiness'],'discovery_only')
        self.assertEqual(r['classification'],'statistical_residual_only');self.assertIn('model',r)
        p['candidate']['relationship']={};self.assertEqual(analyze(p)['readiness'],'discovery_only')
    def test_exact_publication_before_and_after_close_baseline(self):
        p=fixture();r=analyze(p);self.assertEqual(r['event_response']['event_sessions'],4)
        eventdate=p['event']['published_at'][:10]
        p['event']['published_at']=eventdate+'T20:00:00Z';p['event']['known_at']=p['event']['published_at']
        p['candidate']['relationship']['known_at']=p['event']['published_at']
        r=analyze(p);self.assertEqual(r['event_response']['event_sessions'],3)
        self.assertEqual(r['event_response']['baseline_date'],eventdate)
    def test_late_known_rule_and_relationship_force_retrospective(self):
        p=fixture();self.assertFalse(analyze(p)['retrospective_only'])
        p['event']['known_at']=p['as_of'];p['rule']['declared_at']=p['as_of'];p['candidate']['relationship']['known_at']=p['as_of']
        r=analyze(p);self.assertTrue(r['retrospective_only']);self.assertEqual(len(r['retrospective_reasons']),3)
        self.assertIn('model',r)
    def test_stale_preserves_computation_and_regime_break_blocks(self):
        p=fixture();p['rule']['max_data_age_hours']=1
        r=analyze(p);self.assertEqual(r['freshness']['state'],'stale');self.assertIn('model',r)
        self.assertEqual(r['readiness'],'discovery_only')
        p=fixture();p['regime_break']={'detected':True,'source_ref':'synthetic://structural_break'}
        r=analyze(p);self.assertEqual(r['classification'],'model_inapplicable_regime_break');self.assertIn('model',r)
    def test_price_basis_and_timestamp_mismatch(self):
        p=fixture();p['series']['TARGET']['price_basis']='raw';self.assertBlocked(p,'price_basis')
        p=fixture();p['series']['TARGET']['rows'][20]['close_at']=p['series']['TARGET']['rows'][20]['date']+'T19:00:00Z';self.assertBlocked(p,'unaligned_close')
    def test_future_knowledge_and_rule_and_no_baseline(self):
        p=fixture();p['event']['known_at']='2099-01-01T00:00:00Z';self.assertBlocked(p,'future_information')
        p=fixture();p['event']['published_at']='2025-01-01T10:00:00Z';self.assertBlocked(p,'no_pre_event_baseline')
    def test_window_exceeded_descriptive_only(self):
        p=fixture();p['rule']['max_event_sessions']=2;r=analyze(p)
        self.assertEqual(r['readiness'],'discovery_only');self.assertIn('event_response',r)
    def test_common_missing_session_requires_explicit_calendar(self):
        p=fixture();expected=[r['date'] for r in p['series']['BENCH']['rows']]
        p['session_calendar']={'source_ref':'synthetic://calendar','expected_dates':expected}
        for series in p['series'].values():series['rows'].pop(10)
        self.assertBlocked(p,'exchange_calendar_sessions')
    def test_unadjusted_prices_need_corporate_action_review(self):
        p=fixture()
        for series in p['series'].values():series['price_basis']='unadjusted_close'
        r=analyze(p);self.assertEqual(r['readiness'],'discovery_only');self.assertIn('model',r)
    def test_no_order_or_gaussian_probability_on_large_z(self):
        r=analyze(fixture());self.assertTrue(r['no_order_execution']);self.assertTrue(r['compiler_isolated'])
        self.assertGreater(abs(r['descriptive_distribution']['residual_z']),2)
        self.assertIsNone(r['descriptive_distribution']['gaussian_tail_probability'])
        self.assertIsNone(r['reversion_time'])


if __name__=='__main__':unittest.main()
