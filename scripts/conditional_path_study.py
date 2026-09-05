#!/usr/bin/env python3
"""Transparent, offline conditional historical-path research; no order interface."""
from __future__ import annotations
import argparse
import hashlib
import json
import math
import statistics
import sys
from datetime import date, datetime, timedelta
from pathlib import Path


class DataGap(ValueError):
    pass


def stamp(value):
    try:
        result = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        if result.tzinfo is None:
            raise ValueError('timezone required')
        return result
    except (ValueError, TypeError) as exc:
        raise DataGap('invalid timezone-aware timestamp: ' + str(value)) from exc


def number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise DataGap('finite numeric value required')
    return float(value)


def fingerprint(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def validate(data, horizon):
    if not isinstance(data, dict):
        raise DataGap('input must be object')
    if not isinstance(horizon, int) or isinstance(horizon, bool) or horizon < 1:
        raise DataGap('horizon must be positive trading sessions')
    for key in ('symbol', 'as_of', 'provenance', 'sessions', 'rows', 'rule'):
        if not data.get(key):
            raise DataGap('missing required input: ' + key)
    if not isinstance(data['provenance'], dict) or not isinstance(data['rule'], dict) or not isinstance(data['rows'], list) or not isinstance(data['sessions'], list):
        raise DataGap('invalid input container types')
    if any(not isinstance(r, dict) for r in data['rows']):
        raise DataGap('each bar must be object')
    as_of = stamp(data['as_of'])
    p = data['provenance']
    for key in ('source', 'source_ref', 'retrieved_at', 'price_basis', 'pit_evidence_ref', 'calendar_source_ref'):
        if not p.get(key):
            raise DataGap('missing provenance: ' + key)
    if stamp(p['retrieved_at']) < as_of:
        raise DataGap('retrieved_at precedes snapshot as_of')
    if not data.get('retrospective_only') and p['price_basis'] != 'point_in_time_consistent_ohlc':
        raise DataGap('revised/unknown price basis: PIT consistent OHLC evidence required')
    sessions = data['sessions']
    try:
        for day in sessions:
            if date.fromisoformat(day).isoformat() != day:
                raise ValueError()
    except (ValueError, TypeError):
        raise DataGap('invalid session date')
    if sessions != sorted(set(sessions)):
        raise DataGap('sessions must be unique and chronological')
    rows = data['rows']
    if [r.get('date') for r in rows] != sessions:
        raise DataGap('symbol/date alignment or missing expected session; never compress missing bars')
    prior = None
    for r in rows:
        if r.get('symbol', data['symbol']) != data['symbol']:
            raise DataGap('mixed symbol')
        close_at, available = stamp(r.get('close_at')), stamp(r.get('available_at'))
        if not (date.fromisoformat(r['date']) <= close_at.date() <= date.fromisoformat(r['date'])+timedelta(days=1)) or available < close_at or available > as_of:
            raise DataGap('bar unavailable at snapshot or before close')
        if prior is not None and close_at <= prior:
            raise DataGap('nonchronological close timestamps')
        prior = close_at
        o, h, l, c = (number(r.get(k)) for k in ('open', 'high', 'low', 'close'))
        if l <= 0 or h < max(o, c, l) or l > min(o, c):
            raise DataGap('invalid OHLC')
    rule = data['rule']
    for key in ('rule_id', 'declared_at', 'declaration_ref', 'lookback', 'features', 'up_thresholds', 'down_thresholds', 'oos_start', 'min_train_n', 'min_oos_n'):
        if key not in rule:
            raise DataGap('missing rule: ' + key)
    if rule.get('horizon') != horizon:
        raise DataGap('CLI horizon differs from frozen rule horizon')
    lb = rule['lookback']
    if not isinstance(lb, int) or lb < 2 or len(rows) <= lb:
        raise DataGap('insufficient lookback/history')
    if rule['oos_start'] not in sessions:
        raise DataGap('oos_start not an exact session')
    if not data.get('retrospective_only') and stamp(rule['declared_at']) >= stamp(rows[sessions.index(rule['oos_start'])]['close_at']):
        raise DataGap('rule must be declared before OOS start; do not backdate a new rule')
    if not data.get('retrospective_only') and stamp(rule['declared_at']) > as_of:
        raise DataGap('rule declared after snapshot')
    if not isinstance(rule['features'], dict) or not rule['features'] or any(k not in ('trailing_return', 'drawdown', 'realized_vol') for k in rule['features']):
        raise DataGap('only built-in trailing price features supported')
    for bounds in rule['features'].values():
        if not isinstance(bounds, list) or any(number(x) != x for x in bounds) or bounds != sorted(set(bounds)):
            raise DataGap('feature bin edges must be ordered unique finite numbers')
    for key in ('up_thresholds', 'down_thresholds'):
        vals = rule[key]
        if not isinstance(vals, list) or not vals or any(number(x) <= 0 for x in vals) or len(vals) != len(set(vals)):
            raise DataGap('threshold magnitudes must be positive unique fractions')
    if any(x >= 1 for x in rule['down_thresholds']):
        raise DataGap('down thresholds must be below 1')
    for key in ('min_train_n', 'min_oos_n'):
        if not isinstance(rule[key], int) or rule[key] < 1:
            raise DataGap('sample gates must be positive integers')
    return rows, rule


def features(rows, i, rule):
    lb = rule['lookback']
    window = rows[i-lb:i+1]
    # The decision instant is the bar availability, not a retrospectively invented close.
    decision = stamp(rows[i]['available_at'])
    if any(stamp(r['available_at']) > decision for r in window):
        return None
    closes = [r['close'] for r in window]
    returns = [math.log(b/a) for a, b in zip(closes, closes[1:])]
    values = {'trailing_return': closes[-1]/closes[0]-1,
              'drawdown': closes[-1]/max(r['high'] for r in window)-1,
              'realized_vol': statistics.stdev(returns)*math.sqrt(252)}
    bins = {key: sum(values[key] >= edge for edge in edges) for key, edges in rule['features'].items()}
    return {'values': {k: values[k] for k in rule['features']}, 'bins': bins,
            'recent_high': max(r['high'] for r in window)}


def path(rows, i, cutoff, rule):
    """Future starts next session, never includes the origin day's earlier high/low."""
    h = rule['horizon']
    origin = rows[i]
    decision = stamp(origin['available_at'])
    future = []
    for r in rows[i+1:min(i+h+1, cutoff+1)]:
        # Late origin publication means this future session cannot be traded/forecast at origin.
        if stamp(r['close_at']) <= decision or stamp(r['available_at']) > stamp(rows[cutoff]['available_at']):
            break
        future.append(r)
    n = len(future)
    base = origin['close']
    f = features(rows, i, rule)
    target = f['recent_high']
    events = {}
    for side, levels in (('up', rule['up_thresholds']), ('down', rule['down_thresholds'])):
        for level in levels:
            events[f'{side}:{level}'] = next((j for j, r in enumerate(future, 1)
                if (r['high'] >= base*(1+level) if side == 'up' else r['low'] <= base*(1-level))), None)
    events['return_to_recent_high'] = (0 if base >= target else next((j for j, r in enumerate(future, 1) if r['high'] >= target), None))
    complete = n == h
    return {'origin': origin['date'], 'origin_index': i, 'observed_sessions': n, 'complete': complete,
            'terminal_return': future[-1]['close']/base-1 if complete else None,
            'max_favorable_excursion': max([0.0]+[r['high']/base-1 for r in future]) if complete else None,
            'max_adverse_excursion': min([0.0]+[r['low']/base-1 for r in future]) if complete else None,
            'events': events, 'recent_high_target': target,
            'observed_path': [{'step':j,'date':r['date'],
                'close_return':r['close']/base-1,'high_return':r['high']/base-1,'low_return':r['low']/base-1}
                for j,r in enumerate(future,1)]}


def quantile(values, q):
    xs = sorted(values)
    if not xs:
        return None
    pos = (len(xs)-1)*q
    lo = int(pos)
    return xs[lo]+(xs[min(lo+1,len(xs)-1)]-xs[lo])*(pos-lo)


def distribution(values):
    return {'n': len(values), 'mean': statistics.mean(values) if values else None,
            'q05': quantile(values,.05), 'q50': quantile(values,.5), 'q95': quantile(values,.95)}


def wilson(k, n):
    if n == 0:
        return None
    z = 1.959963984540054
    center = (k/n+z*z/(2*n))/(1+z*z/n)
    half = z*math.sqrt(k/n*(1-k/n)/n+z*z/(4*n*n))/(1+z*z/n)
    return [max(0,center-half),min(1,center+half)]


def summarize(paths, rule):
    full = [p for p in paths if p['complete']]
    events = {}
    keys = [f'{side}:{x}' for side in ('up','down') for x in rule[side+'_thresholds']]+['return_to_recent_high']
    for key in keys:
        times = [p['events'][key] for p in full if p['events'][key] is not None]
        known = sum(p['events'][key] is not None for p in paths)
        unresolved = sum(not p['complete'] and p['events'][key] is None for p in paths)
        n = len(paths)
        events[key] = {'complete_window_n': len(full), 'hits':len(times), 'historical_frequency':len(times)/len(full) if full else None,
                       'wilson95_descriptive':wilson(len(times),len(full)), 'first_passage_sessions_given_hit':distribution(times),
                       'horizon_identification_bounds_all_origins': [known/n,(known+unresolved)/n] if n else None,
                       'censored_unresolved_n':unresolved}
    joint = {}
    for u in rule['up_thresholds']:
        for d in rule['down_thresholds']:
            key = f'up:{u}&down:{d}'
            hits = sum(p['events'][f'up:{u}'] is not None and p['events'][f'down:{d}'] is not None for p in full)
            joint[key] = {'hits': hits, 'n':len(full), 'historical_frequency': hits/len(full) if full else None,
                          'order':'unknown when same daily bar; events are not mutually exclusive'}
    return {'actual_n':len(paths), 'complete_n':len(full), 'right_censored_n':len(paths)-len(full),
            'terminal_return':distribution([p['terminal_return'] for p in full]),
            'max_adverse_excursion':distribution([p['max_adverse_excursion'] for p in full]),
            'max_favorable_excursion':distribution([p['max_favorable_excursion'] for p in full]),
            'touch_events':events, 'joint_touch_events':joint}


def sample(rows, cutoff, query_i, rule, conditional=True, mature_only=False):
    query = features(rows, query_i, rule)
    if query is None:
        return []
    result, last = [], -rule['horizon']-1
    for i in range(rule['lookback'], cutoff):
        if stamp(rows[i]['available_at']) > stamp(rows[cutoff]['available_at']):
            continue
        if i <= last+rule['horizon']:
            continue
        f = features(rows, i, rule)
        if f is None or (conditional and f['bins'] != query['bins']):
            continue
        p = path(rows,i,cutoff,rule)
        if mature_only and not p['complete']:
            continue
        result.append(p)
        last = i
    return result


def empirical_path_cone(paths, current_spot, horizon):
    """Only each origin's already observed points; no interpolation across missing future."""
    quantiles={'q025':.025,'q16':.16,'q25':.25,'q50':.50,'q75':.75,'q84':.84,'q975':.975}
    steps=[]
    for step in range(1,horizon+1):
        points=[p['observed_path'][step-1] for p in paths if len(p.get('observed_path',[]))>=step]
        row={'step':step,'n':len(points),'censored_n':len(paths)-len(points)}
        for kind in ('close','high','low'):
            values=[point[kind+'_return'] for point in points]
            qs={name:quantile(values,q) for name,q in quantiles.items()}
            row[kind+'_return']={'n':len(values),**qs}
            row[kind+'_price_projection']={name:current_spot*(1+v) if v is not None else None for name,v in qs.items()}
        steps.append(row)
    return {'method':'empirical matched historical path translation; no Monte Carlo or Gamma adjustment',
            'projection_label':'historical paths translated to latest observed spot; not calibrated future prices',
            'reference_spot':current_spot,'origin_n':len(paths),'steps':steps,
            'ex_ante_calibrated':False,'historical_gamma_conditioning':False,
            'point_definition':'close/high/low of each individual future bar, not cumulative extrema or first touch',
            'censoring':'per-step observed points only; n can decrease; missing future stays absent, never zero-filled',
            'interpretation':'pointwise empirical quantiles, not simultaneous path coverage or threshold touch probabilities'}


def study(data, horizon):
    rows, rule = validate(data,horizon)
    now = len(rows)-1
    conditional = sample(rows,now,now,rule)
    baseline = sample(rows,now,now,rule,False)
    forecasts = []
    start = data['sessions'].index(rule['oos_start'])
    # Fixed stride avoids overlapping OOS label windows; no threshold or feature fitting.
    for i in range(max(start,rule['lookback']),len(rows),horizon+1):
        if features(rows,i,rule) is None:
            forecasts.append({'origin':rows[i]['date'], 'status':'feature_unavailable_at_origin', 'outcome':None})
            continue
        train = sample(rows,i,i,rule,True,True)
        base_train = sample(rows,i,i,rule,False,True)
        report, base_report = summarize(train,rule), summarize(base_train,rule)
        outcome = path(rows,i,now,rule)
        usable = len(train) >= rule['min_train_n'] and len(base_train) >= rule['min_train_n']
        forecasts.append({'origin':rows[i]['date'], 'forecast_as_of':rows[i]['available_at'],
            'train_last_label_date': max((rows[p['origin_index']+horizon]['date'] for p in train),default=None),
            'train_origins':[p['origin'] for p in train], 'train_n':len(train),'baseline_train_n':len(base_train),
            'status':'issued' if usable else 'insufficient_training_sample',
            'touch_probabilities':{k:v['historical_frequency'] for k,v in report['touch_events'].items()} if usable else None,
            'baseline_probabilities':{k:v['historical_frequency'] for k,v in base_report['touch_events'].items()} if usable else None,
            'terminal_interval90':[report['terminal_return']['q05'],report['terminal_return']['q95']] if usable else None,
            'baseline_terminal_interval90':[base_report['terminal_return']['q05'],base_report['terminal_return']['q95']] if usable else None,
            'outcome':outcome})
    scored = [f for f in forecasts if f['status']=='issued' and f['outcome']['complete']]
    scores = {}
    keys = summarize([],rule)['touch_events']
    for k in keys:
        scores[k] = {name:statistics.mean((f[field][k]-int(f['outcome']['events'][k] is not None))**2 for f in scored) if scored else None
                     for name,field in (('brier','touch_probabilities'),('baseline_brier','baseline_probabilities'))}
    intervals = {}
    for prefix in ('','baseline_'):
        intervals[prefix+'coverage90'] = statistics.mean(f[prefix+'terminal_interval90'][0] <= f['outcome']['terminal_return'] <= f[prefix+'terminal_interval90'][1] for f in scored) if scored else None
        intervals[prefix+'mean_width90'] = statistics.mean(f[prefix+'terminal_interval90'][1]-f[prefix+'terminal_interval90'][0] for f in scored) if scored else None
    enough = len(scored) >= rule['min_oos_n']
    gaps = ['historical_nonstationarity_and_residual_dependence', 'no_cost_capacity_or_strategy_validation', 'declaration_ref_requires_external_audit_not_self_authenticating']
    if data.get('retrospective_only'):
        gaps.extend(['historical_pit_unverified', 'rule_not_preregistered_for_historical_oos', 'exchange_calendar_unverified_horizon_counts_observed_bars', 'availability_assumed_for_replay_not_evidence', 'adjustment_revision_risk:' + str(data['provenance'].get('price_basis'))])
    if len(scored)<rule['min_oos_n']:
        gaps.append('insufficient_oos_sample')
    if not conditional or sum(p['complete'] for p in conditional)<rule['min_train_n']:
        gaps.append('insufficient_conditional_sample')
    return {'schema_version':'conditional_path_study.v1', 'symbol':data['symbol'],'as_of':data['as_of'],
            'computation_status':'completed', 'state':'retrospective_only' if data.get('retrospective_only') else 'pit_evidence_supplied_unverified',
            'input_sha256':fingerprint(data),'rule_sha256':fingerprint(rule),'frozen_rule':rule,'provenance':data['provenance'],
            'method_origin':'independent transparent state-bin implementation; not Balder proprietary model',
            'claim_type':'descriptive_historical_frequency', 'ex_ante_calibrated_probability':False,
            'no_order_execution':True,'materiality_eligible':False,'horizon_sessions':horizon,
            'time_unit':('observed daily bars; calendar and historical availability unverified' if data.get('retrospective_only') else 'trading sessions from origin close availability; hit time conditional on hit'),
            'source_freshness':{'state':'clock_reported', 'basis':'latest_query_bar_not_historical_training_origins',
                'clocks':[{'kind':'last_close_at','at':rows[-1]['close_at']}],
                'last_close_at':rows[-1]['close_at'],
                'hours_since_last_close':(stamp(data['as_of'])-stamp(rows[-1]['close_at'])).total_seconds()/3600},
            'query_state':features(rows,now,rule),'sample_period':[rows[0]['date'],rows[-1]['date']],
            'sampling':'earliest matching origin then skip horizon+1; disjoint outcome windows, not iid guarantee',
            'conditional':summarize(conditional,rule),'unconditional_same_cutoff':summarize(baseline,rule),
            'conditional_paths':conditional,'baseline_paths':baseline,
            'empirical_path_cone':empirical_path_cone(conditional,rows[now]['close'],horizon),
            'walk_forward':{'mode':'retrospective frozen-rule replay; declaration evidence not authenticated by this CLI',
                            'forecasts':forecasts,'scored_n':len(scored),'sample_gate':'sufficient_for_descriptive_scoring' if enough else 'insufficient',
                            'proper_scores':scores,**intervals,'calibration_accepted':False},
            'data_gaps':gaps,
            'assumptions':['supplied exchange session calendar is complete and independently sourced',
                'PIT OHLC evidence and original rule declaration must be audited externally',
                'no synthetic paths or future imputation; incomplete endpoints excluded',
                'censored touch outcomes use identification bounds, not zero or an independent-censoring assumption',
                'Wilson intervals are descriptive iid approximations; nonoverlap does not remove regime dependence',
                'daily high/low shows touches, not intraday sequence, fill quality or guaranteed execution']}


def retrospective_panel(panel, rule):
    """Explicit, downgraded adapter; preserves provider metadata, never manufactures PIT evidence."""
    for k in ('symbol','source','fetched_at','adjust_basis','rows'):
        if not panel.get(k):
            raise DataGap('factor panel missing: '+k)
    rows = []
    for raw in panel['rows']:
        r = dict(raw)
        # Timestamp is a replay ordering convention, not exchange availability evidence.
        r['close_at'] = str(r['date'])+'T00:00:00+00:00'
        r['available_at'] = r['close_at']
        rows.append(r)
    return {'symbol':panel['symbol'],'as_of':panel['fetched_at'],'rows':rows,
            'sessions':[r['date'] for r in rows],'rule':rule,'retrospective_only':True,
            'provenance':{'source':panel['source'],'source_ref':'factor_panel input; see raw_input_sha256',
                          'retrieved_at':panel['fetched_at'],'price_basis':panel['adjust_basis'],
                          'pit_evidence_ref':'UNVERIFIED; replay convention only',
                          'calendar_source_ref':'UNVERIFIED; observed row dates only',
                          'raw_input_sha256':fingerprint(panel), 'original_pit_caveats':panel.get('pit_caveats',[])}}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',required=True)
    parser.add_argument('--horizon',type=int,required=True)
    parser.add_argument('--output')
    parser.add_argument('--retrospective-panel',action='store_true',help='explicitly downgraded factor-panel adapter; historical PIT/OOS unverified')
    parser.add_argument('--rule',help='frozen rule JSON required for retrospective-panel mode')
    args = parser.parse_args(argv)
    try:
        data = json.loads(Path(args.input).read_text())
        if args.retrospective_panel:
            if not args.rule:
                raise DataGap('--rule required for retrospective-panel')
            data = retrospective_panel(data,json.loads(Path(args.rule).read_text()))
        result = study(data,args.horizon)
        code = 0
    except (DataGap,ValueError,KeyError,TypeError,OSError,AttributeError,IndexError) as exc:
        result = {'schema_version':'conditional_path_study.v1','status':'blocked','no_order_execution':True,
                  'materiality_eligible':False,'data_gaps':[str(exc)]}
        code = 2
    raw = json.dumps(result,indent=2,ensure_ascii=False,allow_nan=False)+'\n'
    if args.output:
        Path(args.output).write_text(raw)
    else:
        print(raw,end='')
    return code


if __name__=='__main__':
    sys.exit(main())
