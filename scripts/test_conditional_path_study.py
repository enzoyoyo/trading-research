"""Synthetic unit fixtures only; never market observations or calibrated claims."""
import copy
import unittest
from datetime import date, timedelta
from conditional_path_study import study, path, validate, DataGap, retrospective_panel, empirical_path_cone


def fixture(n=80):
    rows=[]
    for i in range(n):
        day=(date(2020,1,1)+timedelta(days=i)).isoformat()
        close=100+(i%7)
        rows.append({'date':day,'open':close,'high':close+3,'low':close-3,'close':close,
                     'close_at':day+'T20:00:00Z','available_at':day+'T20:01:00Z'})
    return {'symbol':'SYNTHETIC','as_of':rows[-1]['available_at'],'sessions':[r['date'] for r in rows],'rows':rows,
            'provenance':{'source':'synthetic_test_only','source_ref':'unit fixture','retrieved_at':rows[-1]['available_at'],
                          'price_basis':'point_in_time_consistent_ohlc','pit_evidence_ref':'synthetic','calendar_source_ref':'synthetic'},
            'rule':{'rule_id':'synthetic_test','declaration_ref':'synthetic fixture, not real preregistration','declared_at':'2019-12-01T00:00:00Z',
                    'horizon':3,'lookback':3,'features':{'drawdown':[]},'up_thresholds':[.02,.04],'down_thresholds':[.02,.04],
                    'oos_start':rows[20]['date'],'min_train_n':2,'min_oos_n':10}}


class ConditionalPathTests(unittest.TestCase):
    def test_source_clock_is_latest_query_close_not_refresh_or_training_clock(self):
        data=fixture(); close=data['rows'][-1]['close_at']
        first=study(data,3)
        self.assertEqual(first['source_freshness']['last_close_at'],close)
        self.assertEqual(len(first['source_freshness']['clocks']),1)
        data['as_of']='2026-09-05T12:00:00Z'
        data['provenance']['retrieved_at']=data['as_of']
        second=study(data,3)
        self.assertEqual(second['source_freshness']['last_close_at'],close)
        self.assertGreater(second['source_freshness']['hours_since_last_close'],1000)

    def test_touch_not_terminal_and_joint(self):
        d=fixture(); i=4
        for r in d['rows'][i:i+4]:
            r.update(open=100,high=101,low=99,close=100)
        d['rows'][i+1].update(high=110,low=90,close=99)
        d['rows'][i+3].update(open=99,high=100,low=98,close=99)
        p=path(d['rows'],i,i+3,d['rule'])
        self.assertAlmostEqual(p['terminal_return'],-.01)
        self.assertEqual(p['events']['up:0.04'],1)
        self.assertEqual(p['events']['down:0.04'],1)
        self.assertEqual(p['events']['up:0.02'],1)
        self.assertAlmostEqual(p['max_favorable_excursion'],.1)
        self.assertAlmostEqual(p['max_adverse_excursion'],-.1)

    def test_censor_not_zero(self):
        d=fixture(81); p=path(d['rows'],78,79,d['rule'])
        self.assertFalse(p['complete']); self.assertIsNone(p['terminal_return'])
        r=study(d,3)
        self.assertGreater(r['conditional']['right_censored_n'],0)
        for event in r['conditional']['touch_events'].values():
            self.assertEqual(event['complete_window_n'],r['conditional']['complete_n'])

    def test_no_lookahead(self):
        d=fixture(); a=study(d,3)
        for r in d['rows'][40:]:
            for key in ('open','high','low','close'): r[key]*=10
        b=study(d,3)
        aa=[f for f in a['walk_forward']['forecasts'] if f['origin']<d['rows'][40]['date']]
        bb=[f for f in b['walk_forward']['forecasts'] if f['origin']<d['rows'][40]['date']]
        for first,second in zip(aa,bb):
            for key in ('touch_probabilities','train_origins','terminal_interval90'):
                self.assertEqual(first[key],second[key])
            if first['train_last_label_date']:
                self.assertLessEqual(first['train_last_label_date'],first['origin'])

    def test_cone_per_step_sample_and_spot_translation(self):
        d=fixture(); paths=[path(d['rows'],75,79,d['rule']),path(d['rows'],78,79,d['rule'])]
        cone=empirical_path_cone(paths,200,3)
        self.assertEqual([s['n'] for s in cone['steps']],[2,1,1])
        self.assertEqual([s['censored_n'] for s in cone['steps']],[0,1,1])
        for row in cone['steps']:
            for kind in ('close','high','low'):
                for q in ('q025','q16','q25','q50','q75','q84','q975'):
                    self.assertAlmostEqual(row[kind+'_price_projection'][q],200*(1+row[kind+'_return'][q]))
        self.assertFalse(cone['ex_ante_calibrated'])

    def test_cone_missing_future_never_filled(self):
        d=fixture(); partial=path(d['rows'],78,79,d['rule'])
        cone=empirical_path_cone([partial],d['rows'][-1]['close'],3)
        self.assertEqual(len(partial['observed_path']),1)
        for row in cone['steps'][1:]:
            self.assertEqual(row['n'],0);self.assertEqual(row['censored_n'],1)
            self.assertIsNone(row['close_return']['q50'])
            self.assertIsNone(row['high_price_projection']['q975'])
        self.assertNotEqual(cone['steps'][0]['close_return']['q50'],cone['steps'][0]['high_return']['q50'])

    def test_nonoverlapping(self):
        r=study(fixture(),3)
        ids=[p['origin_index'] for p in r['conditional_paths']]
        self.assertTrue(all(b-a>3 for a,b in zip(ids,ids[1:])))

    def test_invalid_symbol_date_missing_and_ohlc(self):
        for change in ('symbol','date','missing','ohlc'):
            d=fixture()
            if change=='symbol': d['rows'][6]['symbol']='OTHER'
            elif change=='date': d['rows'][6]['date']=d['rows'][5]['date']
            elif change=='missing': d['rows'].pop(6)
            else: d['rows'][6]['low']=200
            with self.subTest(change=change), self.assertRaises(DataGap): study(d,3)

    def test_late_historical_bar_not_used_before_availability(self):
        d=fixture()
        d['rows'][19]['available_at']=d['rows'][30]['available_at']
        r=study(d,3)
        self.assertEqual(r['walk_forward']['forecasts'][0]['status'],'feature_unavailable_at_origin')

    def test_unavailable_bar_rejected(self):
        d=fixture(); d['rows'][4]['available_at']='2040-01-01T00:00:00Z'
        with self.assertRaises(DataGap): study(d,3)

    def test_revision_and_posthoc_rules_rejected_in_strict_mode(self):
        d=fixture(); d['provenance']['price_basis']='forward'
        with self.assertRaises(DataGap): study(d,3)
        d=fixture(); d['rule']['declared_at']=d['rows'][30]['close_at']
        with self.assertRaises(DataGap): study(d,3)
        with self.assertRaises(DataGap): study(fixture(),7)

    def test_small_sample_never_calibrated(self):
        r=study(fixture(24),3)
        self.assertFalse(r['ex_ante_calibrated_probability'])
        self.assertEqual(r['walk_forward']['sample_gate'],'insufficient')
        self.assertIn('insufficient_oos_sample',r['data_gaps'])

    def test_return_high_origin_zero_or_future_first(self):
        d=fixture(); i=6
        d['rows'][i]['high']=d['rows'][i]['close']
        # Prior highs are greater, so origin has not recovered target.
        p=path(d['rows'],i,i+3,d['rule'])
        self.assertNotEqual(p['events']['return_to_recent_high'],0)
        d['rows'][i].update(open=120,high=120,low=120,close=120)
        self.assertEqual(path(d['rows'],i,i+3,d['rule'])['events']['return_to_recent_high'],0)

    def test_retrospective_computes_without_pit_claim(self):
        d=fixture()
        p={'symbol':'SYNTHETIC','source':'synthetic','fetched_at':d['as_of'],'adjust_basis':'forward_test','rows':d['rows']}
        r=study(retrospective_panel(p,d['rule']),3)
        self.assertEqual(r['state'],'retrospective_only')
        self.assertGreater(r['conditional']['actual_n'],0)
        self.assertFalse(r['walk_forward']['calibration_accepted'])
        self.assertIn('historical_pit_unverified',r['data_gaps'])


if __name__=='__main__': unittest.main()
