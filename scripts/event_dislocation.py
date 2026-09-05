#!/usr/bin/env python3
"""Point-in-time event spillover residuals. Research only; no price targets or orders.

Frozen pre-event partial OLS removes benchmark co-movement before estimating issuer
spillover. Event-horizon nonoverlapping training blocks supply a descriptive z,
never a reversion probability. Input contract: references/event-dislocation.md.
"""
from __future__ import annotations
import argparse
from datetime import date, datetime, timezone
import json
import math
from pathlib import Path
import statistics


class DataGap(ValueError):
    pass


def number(value, name, minimum=None, integer=False):
    if value is None or isinstance(value,bool): raise DataGap(f'{name}:missing_or_invalid')
    try: v=float(value)
    except (TypeError,ValueError,OverflowError): raise DataGap(f'{name}:invalid_number') from None
    if not math.isfinite(v) or (minimum is not None and v<minimum): raise DataGap(f'{name}:out_of_range')
    if integer and v!=int(v): raise DataGap(f'{name}:integer_required')
    return int(v) if integer else v


def timestamp(value,name):
    try:
        t=datetime.fromisoformat(str(value).replace('Z','+00:00'))
        if t.tzinfo is None: raise ValueError()
        return t.astimezone(timezone.utc)
    except (TypeError,ValueError): raise DataGap(f'{name}:timezone_timestamp_required') from None


def text_field(obj,key):
    v=obj.get(key)
    if not isinstance(v,str) or not v.strip(): raise DataGap(f'{key}:missing')
    return v


def centered(x):
    m=statistics.fmean(x)
    return m,[v-m for v in x]


def dot(x,y): return math.fsum(a*b for a,b in zip(x,y))


def corr(x,y):
    _,a=centered(x);_,b=centered(y);den=dot(a,a)*dot(b,b)
    return dot(a,b)/math.sqrt(den) if den>1e-48 else None


def partial_fit(benchmark,issuer,target):
    """Frisch-Waugh-Lovell partial regression, with explicit singularity checks."""
    n=len(benchmark)
    if n<6 or len(issuer)!=n or len(target)!=n: raise DataGap('training:insufficient_or_unaligned_returns')
    mb,bc=centered(benchmark);mi,ic=centered(issuer);mt,tc=centered(target)
    vb=dot(bc,bc)
    if vb<=1e-24: raise DataGap('model:singular_benchmark_variance')
    issuer_beta_b=dot(bc,ic)/vb; target_beta_b=dot(bc,tc)/vb
    issuer_alpha=mi-issuer_beta_b*mb; target_alpha=mt-target_beta_b*mb
    ir=[i-issuer_beta_b*b for i,b in zip(ic,bc)]
    tr=[t-target_beta_b*b for t,b in zip(tc,bc)]
    vi=dot(ir,ir)
    if vi<=max(1e-24,dot(ic,ic)*1e-10): raise DataGap('model:singular_issuer_after_benchmark')
    beta_i=dot(ir,tr)/vi
    beta_b=target_beta_b-beta_i*issuer_beta_b
    alpha=target_alpha-beta_i*issuer_alpha
    residual=[t-alpha-beta_b*b-beta_i*i for t,b,i in zip(target,benchmark,issuer)]
    sigma=math.sqrt(dot(residual,residual)/(n-3))
    return {'alpha_per_session':alpha,'beta_benchmark':beta_b,'beta_issuer':beta_i,
            'issuer_alpha_per_session':issuer_alpha,'issuer_beta_benchmark':issuer_beta_b,
            'target_benchmark_only_alpha_per_session':target_alpha,'target_benchmark_only_beta':target_beta_b,
            'training_n':n,'training_residual_sigma_per_session':sigma,
            'training_raw_target_issuer_corr':corr(target,issuer),'training_partial_target_issuer_corr':corr(tr,ir),
            'residuals':residual}


def read_series(name,raw,asof):
    source=text_field(raw,'source_ref'); basis=text_field(raw,'price_basis')
    retrieved=timestamp(raw.get('retrieved_at'),f'{name}.retrieved_at')
    if retrieved>asof: raise DataGap(f'{name}:future_retrieval')
    rows=raw.get('rows')
    if not isinstance(rows,list) or not rows: raise DataGap(f'{name}:rows_missing')
    parsed={}
    for row in rows:
        ds=text_field(row,'date')
        try: date.fromisoformat(ds)
        except ValueError: raise DataGap(f'{name}:invalid_session_date') from None
        if ds in parsed: raise DataGap(f'{name}:duplicate_session_date')
        close=number(row.get('close'),f'{name}.close',0)
        if close<=0: raise DataGap(f'{name}:nonpositive_price')
        ct=timestamp(row.get('close_at'),f'{name}.close_at')
        if ct.date().isoformat()!=ds: raise DataGap(f'{name}:date_close_at_mismatch_US_daily_required')
        if ct>asof: raise DataGap(f'{name}:future_close')
        if ct>retrieved: raise DataGap(f'{name}:close_after_retrieval')
        parsed[ds]={'close':close,'at':ct}
    return parsed,{'source_ref':source,'retrieved_at':raw['retrieved_at'],'price_basis':basis,'rows':len(parsed),
                   'close_at_basis':raw.get('close_at_basis'),'close_at_source_ref':raw.get('close_at_source_ref')}


def analyze(payload):
    result={'schema':'event_dislocation.v1','no_order_execution':True,'hypothesis_only':True,
        'compiler_isolated':True,'readiness':'blocked','classification':'unavailable','data_gaps':['forward_estimates:estimator_not_implemented'],
        'reversion_probability':None,'reversion_time':None,'price_target':None,'expected_return':None,'net_edge':None,
        'forward_estimation_capability':{'status':'not_implemented',
            'fields':['reversion_probability','reversion_time','price_target','expected_return','net_edge'],
            'additional_data_alone_enables_estimation':False},
        'probability_evidence_gap':'event_conditional_forward_estimator_not_implemented',
        'claim_boundary':'relative_to_frozen_statistical_model_not_fair_value_or_proven_mispricing'}
    try:
        if not isinstance(payload,dict): raise DataGap('payload:object_required')
        asof=timestamp(payload.get('as_of'),'as_of');result['as_of']=payload['as_of']
        event=payload.get('event',{});cand=payload.get('candidate',{});rule=payload.get('rule',{})
        eventid=text_field(event,'event_id');issuer=text_field(event,'issuer');target=text_field(cand,'symbol')
        benchmark=text_field(payload,'benchmark');text_field(event,'source_ref');text_field(rule,'rule_id')
        if len({issuer,target,benchmark})!=3: raise DataGap('identity:target_issuer_benchmark_must_differ')
        published=timestamp(event.get('published_at'),'event.published_at');known=timestamp(event.get('known_at'),'event.known_at')
        declared=timestamp(rule.get('declared_at'),'rule.declared_at')
        if published>asof or known>asof or declared>asof: raise DataGap('event_or_rule:future_information')
        if known<published: raise DataGap('event.known_at:before_publication')
        if not isinstance(payload.get('retrospective_only'),bool): raise DataGap('retrospective_only:explicit_boolean_required')
        lookback=number(rule.get('lookback'),'rule.lookback',6,True)
        cutoff=number(rule.get('min_abs_residual_z'),'rule.min_abs_residual_z',0)
        maxage=number(rule.get('max_event_sessions'),'rule.max_event_sessions',1,True)
        retrospective_reasons=[]
        if known>published: retrospective_reasons.append('event_evidence_known_after_event')
        if declared>published: retrospective_reasons.append('rule_declared_after_event_no_backfilled_precommitment')
        if payload['retrospective_only']: retrospective_reasons.append('explicit_retrospective_request')
        relationship=cand.get('relationship',{})
        relationship_gaps=[]
        try:
            kind=text_field(relationship,'kind');text_field(relationship,'source_ref');text_field(relationship,'description')
            if kind not in ('commercial_partnership','co_development','supplier_customer','ownership','competitor','shared_product_exposure'):
                raise DataGap('relationship:unsupported_or_unsubstantiated_kind')
            rk=timestamp(relationship.get('known_at'),'relationship.known_at')
            if rk>asof: raise DataGap('relationship:future_evidence')
            if rk>published: retrospective_reasons.append('relationship_evidence_known_after_event')
            if relationship.get('issuer',issuer)!=issuer or relationship.get('target',target)!=target:
                raise DataGap('relationship:issuer_target_mismatch')
        except (DataGap,AttributeError,TypeError) as e:
            relationship_gaps.append(str(e))
        result.update({'event_id':eventid,'issuer':issuer,'target':target,'benchmark':benchmark,
            'rule_id':rule['rule_id'],'event_source_ref':event['source_ref'],
            'retrospective_only':bool(retrospective_reasons),'retrospective_reasons':retrospective_reasons,
            'relationship_evidence':relationship,'relationship_data_gaps':relationship_gaps})
        series=payload.get('series',{});tables={};provenance={}
        for symbol in (benchmark,issuer,target):
            tables[symbol],provenance[symbol]=read_series(symbol,series.get(symbol,{}),asof)
        dates=sorted(tables[benchmark])
        if any(set(tables[s])!=set(dates) for s in (issuer,target)): raise DataGap('series:missing_or_unaligned_session_dates')
        if len({p['price_basis'] for p in provenance.values()})!=1: raise DataGap('series:inconsistent_price_basis')
        calendar=payload.get('session_calendar')
        if calendar is not None:
            text_field(calendar,'source_ref')
            expected=calendar.get('expected_dates')
            if not isinstance(expected,list) or len(expected)!=len(set(expected)) or set(expected)!=set(dates):
                raise DataGap('series:missing_or_extra_exchange_calendar_sessions')
        else: result['data_gaps'].append('calendar:common_missing_sessions_not_independently_checked')
        price_review_gap=(any(token in provenance[benchmark]['price_basis'].lower() for token in ('raw','unadjusted','none'))
                          and not payload.get('corporate_action_review_ref'))
        if price_review_gap: result['data_gaps'].append('price_basis:unadjusted_series_corporate_actions_not_reviewed')
        for d in dates:
            if len({tables[s][d]['at'] for s in tables})!=1: raise DataGap('series:unaligned_close_times')
        baseline_candidates=[j for j,d in enumerate(dates) if tables[benchmark][d]['at']<=published]
        if not baseline_candidates: raise DataGap('event:no_pre_event_baseline')
        base=baseline_candidates[-1]
        if base<lookback: raise DataGap('training:insufficient_pre_event_lookback')
        event_days=dates[base+1:];age=len(event_days)
        if age<1: raise DataGap('event:no_post_event_closed_session')
        training_dates=dates[base-lookback:base+1]
        returns={s:[math.log(tables[s][b]['close'])-math.log(tables[s][a]['close'])
                    for a,b in zip(training_dates,training_dates[1:])] for s in tables}
        fit=partial_fit(returns[benchmark],returns[issuer],returns[target])
        residuals=fit.pop('residuals')
        result['model']={**fit,'method':'pre_event_FWL_partial_OLS_with_intercept',
            'training_baseline_date':dates[base],'training_baseline_close_at':tables[benchmark][dates[base]]['at'].isoformat(),
            'training_first_return_start_date':training_dates[0],'training_last_return_end_date':training_dates[-1],
            'post_event_rows_used_in_fit':0,'price_basis':provenance[benchmark]['price_basis']}
        cumulative={s:math.log(tables[s][dates[-1]]['close'])-math.log(tables[s][dates[base]]['close']) for s in tables}
        ir=cumulative[issuer]-fit['issuer_alpha_per_session']*age-fit['issuer_beta_benchmark']*cumulative[benchmark]
        tr=cumulative[target]-fit['target_benchmark_only_alpha_per_session']*age-fit['target_benchmark_only_beta']*cumulative[benchmark]
        spill=fit['beta_issuer']*ir
        extra=tr-spill
        predicted=fit['alpha_per_session']*age+fit['beta_benchmark']*cumulative[benchmark]+fit['beta_issuer']*cumulative[issuer]
        # End-anchored complete NONOVERLAPPING blocks; leftover oldest returns discarded.
        count=len(residuals)//age
        blocks=[math.fsum(residuals[len(residuals)-(j+1)*age:len(residuals)-j*age]) for j in range(count)]
        blockmean=statistics.fmean(blocks) if blocks else None
        blocksigma=statistics.stdev(blocks) if len(blocks)>=2 else None
        z=(extra-blockmean)/blocksigma if count>=3 and blocksigma is not None and blocksigma>1e-12 else None
        if z is None: result['data_gaps'].append('descriptive_z:insufficient_nonoverlapping_blocks_or_zero_variance')
        result['event_response']={'event_sessions':age,'baseline_date':dates[base],'last_session_date':dates[-1],
            'observed_cumulative_logreturns':cumulative,'target_benchmark_abnormal_logreturn':tr,
            'issuer_benchmark_abnormal_logreturn':ir,'spillover_predicted_logresponse':spill,
            'extra_residual_logreturn':extra,'model_predicted_target_logreturn':predicted,
            'estimated_dislocation_pct':100*math.expm1(extra),
            'relative_side':'laggard' if extra<0 else 'rich' if extra>0 else 'at_model',
            'meaning':'conditional_relative_residual_not_reversion_forecast'}
        result['descriptive_distribution']={'method':'pre_event_nonoverlapping_same_horizon_residual_sums',
            'horizon_sessions':age,'block_count':count,'discarded_oldest_return_count':len(residuals)%age,
            'block_mean':blockmean,'block_sample_sigma':blocksigma,'residual_z':z,
            'independence_assumed':False,'gaussian_tail_probability':None,
            'interpretation':'descriptive_scale_only_blocks_may_remain_serially_dependent_no_event_reversion_probability'}
        latest=tables[benchmark][dates[-1]]['at'];hours=(asof-latest).total_seconds()/3600
        freshness_limit=rule.get('max_data_age_hours')
        fresh='unknown_policy'
        if freshness_limit is not None:
            fresh='fresh' if hours<=number(freshness_limit,'rule.max_data_age_hours',0) else 'stale'
        else: result['data_gaps'].append('freshness:max_data_age_hours_not_supplied')
        result['freshness']={'state':fresh,'hours_since_last_close':hours,'last_close_at':latest.isoformat(),
                             'calculations_preserved_if_stale':True,'event_window_state':'within_rule' if age<=maxage else 'outside_rule'}
        result['sources']=provenance
        result['event_baseline_prices']={s:tables[s][dates[base]]['close'] for s in tables}
        result['latest_prices']={s:tables[s][dates[-1]]['close'] for s in tables}
        regime_gaps=[]
        if payload.get('regime_break',{}).get('detected') is True:
            regime_gaps.append('regime_break:externally_flagged_model_inapplicable')
        shift_limit=rule.get('max_coefficient_shift')
        if shift_limit is not None:
            limit=number(shift_limit,'rule.max_coefficient_shift',0)
            split=lookback//2
            first=partial_fit(returns[benchmark][:split],returns[issuer][:split],returns[target][:split])
            second=partial_fit(returns[benchmark][split:],returns[issuer][split:],returns[target][split:])
            shifts={k:abs(first[k]-second[k]) for k in ('beta_benchmark','beta_issuer')}
            result['coefficient_stability']={'absolute_half_window_shifts':shifts,'predeclared_limit':limit,
                'interpretation':'diagnostic_not_proof_of_regime_stability'}
            if max(shifts.values())>limit: regime_gaps.append('regime_break:coefficient_shift_limit_exceeded')
        else: result['data_gaps'].append('regime_stability:not_independently_validated')
        result['regime_data_gaps']=regime_gaps
        if regime_gaps:
            result['readiness']='blocked';result['classification']='model_inapplicable_regime_break'
        elif relationship_gaps:
            result['readiness']='discovery_only';result['classification']='statistical_residual_only'
        elif age>maxage or z is None or fresh!='fresh' or price_review_gap:
            result['readiness']='discovery_only';result['classification']='statistical_residual_only'
        else:
            result['readiness']='hypothesis_only'
            result['classification']='relative_dislocation_candidate' if abs(z)>=cutoff else 'no_threshold_dislocation'
        result['threshold']={'min_abs_residual_z':cutoff,'met':abs(z)>=cutoff if z is not None else None,
                             'rule_declared_at':rule['declared_at'],'does_not_establish_economic_causality':True}
        result['data_gaps'].extend(relationship_gaps+regime_gaps)
        result['data_gaps'].append('net_edge:estimator_not_implemented_requires_separate_validated_forward_payoff_and_cost_model')
        result['refresh_triggers']=['new_closed_session','event_revision','relationship_changed','regime_break','data_stale']
    except (DataGap,KeyError,TypeError,ValueError,AttributeError,OverflowError) as e:
        result['readiness']='blocked';result['classification']='unavailable';result['data_gaps'].append(str(e))
    return result


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    group=parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--input');group.add_argument('--self-test',action='store_true')
    parser.add_argument('--output');args=parser.parse_args(argv)
    if args.self_test:
        import unittest
        suite=unittest.defaultTestLoader.discover(str(Path(__file__).parent),pattern='test_event_dislocation.py')
        return 0 if unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful() else 1
    result=analyze(json.loads(Path(args.input).read_text()))
    output=json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False)
    if args.output: Path(args.output).write_text(output+'\n')
    else: print(output)
    return 2 if result['readiness']=='blocked' else 0


if __name__=='__main__':raise SystemExit(main())
