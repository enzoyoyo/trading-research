#!/usr/bin/env python3
"""Read-only option expressions, exact expiry geometry and conditional hedge scenarios.

No broker imports, prediction generation, Compiler signals, or order path.
See references/options-expression-lab.md for the deliberately strict input contract.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import math
import re
from pathlib import Path


class Gap(ValueError):
    pass


def number(value, name, minimum=None):
    if isinstance(value, bool) or value is None:
        raise Gap(f"{name}:missing_or_invalid_number")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise Gap(f"{name}:invalid_number") from None
    if not result.is_finite() or (minimum is not None and result < Decimal(str(minimum))):
        raise Gap(f"{name}:out_of_range")
    return result


def stamp(value, name):
    try:
        result = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        if result.tzinfo is None:
            raise ValueError()
        return result.astimezone(timezone.utc)
    except (ValueError, TypeError):
        raise Gap(f"{name}:timezone_timestamp_required") from None


def required(row, key):
    v = row.get(key)
    if not isinstance(v, str) or not v.strip():
        raise Gap(f"{key}:missing")
    return v


def scalar(value):
    """Decimal results remain decimal strings; infinities are explicit labels."""
    return format(value, 'f') if isinstance(value, Decimal) else value


def valuation_terms(model, underlying):
    if 'by_underlying' not in model:
        return model
    terms = model['by_underlying']
    if not isinstance(terms, dict) or not isinstance(terms.get(underlying), dict):
        raise Gap('model.by_underlying:missing_terms_for:' + underlying)
    return terms[underlying]


def payoff(legs, spot):
    s = number(spot, 'terminal_price', 0)
    return sum((l['signed_qty'] * l['multiplier'] * max(Decimal(0),
                (s-l['strike']) if l['right'] == 'call' else (l['strike']-s))
                for l in legs), Decimal(0))


def expiry_geometry(legs, total_cost):
    """Exact piecewise linear extrema and roots on S >= 0; no finite-grid max claim."""
    knots = sorted({Decimal(0)} | {l['strike'] for l in legs})
    vals = [payoff(legs, s)-total_cost for s in knots]
    tail_slope = sum((l['signed_qty']*l['multiplier'] for l in legs if l['right']=='call'), Decimal(0))
    roots, intervals = set(), []
    for a,b,fa,fb in zip(knots,knots[1:],vals,vals[1:]):
        if fa == 0: roots.add(a)
        if fa == fb == 0: intervals.append([scalar(a),scalar(b)])
        elif fa*fb < 0: roots.add(a-fa*(b-a)/(fb-fa))
    if vals[-1] == 0:
        roots.add(knots[-1])
        if tail_slope == 0: intervals.append([scalar(knots[-1]), 'infinity'])
    elif tail_slope and -vals[-1]/tail_slope > 0:
        roots.add(knots[-1]-vals[-1]/tail_slope)
    return {'max_profit': 'unbounded' if tail_slope>0 else scalar(max(Decimal(0),max(vals))),
            'maximum_pnl': 'infinity' if tail_slope>0 else scalar(max(vals)),
            'max_loss': 'unbounded' if tail_slope<0 else scalar(max(Decimal(0),-min(vals))),
            'minimum_pnl': '-infinity' if tail_slope<0 else scalar(min(vals)),
            'breakeven_prices': [scalar(x) for x in sorted(roots)],
            'zero_pnl_intervals': intervals,
            'basis': 'contractual_expiry_intrinsic_less_explicit_costs',
            'not_early_assignment_or_post_expiry_stock_risk_bound': True}


def bs_european(spot, strike, years, iv, rate, dividend, right):
    """Continuous yield European Black-Scholes; price/greeks are model marks only."""
    s,k,t,v,r,q = (float(number(x,n)) for x,n in zip(
        [spot,strike,years,iv,rate,dividend],['spot','strike','years','iv','rate','dividend_yield']))
    if min(s,k,t,v)<=0 or right not in ('call','put'):
        raise Gap('black_scholes:positive_spot_strike_time_iv_required')
    n = lambda x: (1+math.erf(x/math.sqrt(2)))/2
    phi = lambda x: math.exp(-x*x/2)/math.sqrt(2*math.pi)
    d1=(math.log(s/k)+(r-q+v*v/2)*t)/(v*math.sqrt(t)); d2=d1-v*math.sqrt(t)
    eq,er=math.exp(-q*t),math.exp(-r*t)
    if right=='call':
        price=s*eq*n(d1)-k*er*n(d2); delta=eq*n(d1)
        theta=-s*eq*phi(d1)*v/(2*math.sqrt(t))-r*k*er*n(d2)+q*s*eq*n(d1)
    else:
        price=k*er*n(-d2)-s*eq*n(-d1); delta=eq*(n(d1)-1)
        theta=-s*eq*phi(d1)*v/(2*math.sqrt(t))+r*k*er*n(-d2)-q*s*eq*n(-d1)
    result={'price':price,'delta':delta,'gamma':eq*phi(d1)/(s*v*math.sqrt(t)),
            'vega_per_unit_iv':s*eq*phi(d1)*math.sqrt(t),'theta_per_calendar_day':theta/365}
    if not all(math.isfinite(x) for x in result.values()): raise Gap('black_scholes:nonfinite')
    return result


def validate_candidate(candidate, payload):
    now=stamp(payload.get('as_of'),'as_of'); policy=payload.get('policy',{})
    age=number(policy.get('max_quote_age_seconds'),'max_quote_age_seconds',0)
    spread_limit=number(policy.get('max_spread_fraction'),'max_spread_fraction',0)
    skew=number(policy.get('max_quote_skew_seconds'),'max_quote_skew_seconds',0)
    units=number(candidate.get('units'),'units',1)
    if units != units.to_integral_value(): raise Gap('units:integer_required')
    rawlegs=candidate.get('legs')
    if not isinstance(rawlegs,list) or not rawlegs: raise Gap('legs:missing')
    legs=[]; times=[]
    for raw in rawlegs:
        l=dict(raw)
        for key in ('contract_id','underlying','expiry','currency','exercise_style','settlement_style',
                    'settlement_session','identity_evidence_ref','quote_source','quote_asof','last_trade_at'):
            required(l,key)
        if l.get('right') not in ('call','put'): raise Gap('right:invalid')
        if l['exercise_style'] not in ('european','american'): raise Gap('exercise_style:unknown')
        if l['settlement_style'] not in ('cash','physical'): raise Gap('settlement_style:unknown')
        if l['settlement_session'] not in ('AM','PM'): raise Gap('settlement_session:unknown')
        if l.get('standard_deliverable_verified') is not True: raise Gap('deliverable:unverified_or_adjusted')
        for key in ('strike','multiplier','signed_qty','bid','ask','bid_size','ask_size'):
            l[key]=number(l.get(key),key,0 if key != 'signed_qty' else None)
        if not l['signed_qty'] or l['signed_qty']!=l['signed_qty'].to_integral_value(): raise Gap('signed_qty:nonzero_integer_required')
        if l['strike']<=0 or l['multiplier']<=0: raise Gap('identity:nonpositive_strike_multiplier')
        qt=stamp(l['quote_asof'],'quote_asof'); exp=stamp(l['expiry'],'expiry'); lt=stamp(l['last_trade_at'],'last_trade_at')
        if lt>exp: raise Gap('last_trade_at:after_settlement')
        # Expiry is judged before any quote rule so an expired contract is never reported as a stale quote.
        if now>=exp: raise Gap('contract:expired:'+l['contract_id'])
        if now>=lt: raise Gap('contract:no_longer_trading:'+l['contract_id'])
        if not 0 <= (now-qt).total_seconds() <= float(age): raise Gap('quote:stale_or_future')
        times.append(qt)
        if l['bid']<0 or l['ask']<=0 or l['ask']<l['bid']: raise Gap('quote:crossed_or_invalid')
        if (l['ask']-l['bid'])/l['ask']>spread_limit: raise Gap('liquidity:spread_limit')
        for k in ('bid_size','ask_size'):
            if l[k]!=l[k].to_integral_value(): raise Gap(f'{k}:integer_contract_count_required')
        if l['signed_qty']<0 and (l['bid']<=0 or l['bid_size']<=0): raise Gap('liquidity:no_sell_bid')
        if l['signed_qty']>0 and l['ask_size']<=0: raise Gap('liquidity:no_buy_ask')
        if l['underlying']=='SPX':
            # Standard SPX vs SPXW identity must be specified, never inferred from a date.
            if (l.get('option_root') not in ('SPX','SPXW') or l['multiplier']!=100 or
                l['exercise_style']!='european' or l['settlement_style']!='cash' or
                l['settlement_session']!=('AM' if l.get('option_root')=='SPX' else 'PM')):
                raise Gap('SPX:standard_product_identity_mismatch_or_FLEX_unsupported')
        legs.append(l)
    if (max(times)-min(times)).total_seconds()>float(skew): raise Gap('quote:asynchronous_legs')
    for key in ('underlying','expiry','right','multiplier','currency','exercise_style','settlement_style','settlement_session'):
        if len({str(l[key]) for l in legs})!=1: raise Gap(f'structure:mixed_{key}')
    if len({l['contract_id'] for l in legs}) != len(legs): raise Gap('structure:duplicate_contract')
    legs.sort(key=lambda x:x['strike'])
    qs=[l['signed_qty'] for l in legs]; ks=[l['strike'] for l in legs]
    kind=candidate.get('kind')
    if kind=='single': valid=len(legs)==1 and abs(qs[0])==1
    elif kind=='vertical': valid=len(legs)==2 and ks[0]<ks[1] and abs(qs[0])==1 and qs[0]==-qs[1]
    elif kind=='butterfly': valid=len(legs)==3 and qs==[1,-2,1] and ks[0]<ks[1]<ks[2] and ks[1]-ks[0]==ks[2]-ks[1]
    else: raise Gap('kind:unsupported')
    if not valid: raise Gap('structure:invalid_strikes_or_signed_ratios')
    for l in legs:
        occ=re.fullmatch(r'([A-Z]+)\s*(\d{6})([CP])(\d{8})',l['contract_id'])
        if not occ: raise Gap('identity:standard_US_OCC_contract_id_required')
        root, date, right, strike=occ.groups()
        if (root != l.get('option_root',l['underlying']) or
            date != stamp(l['expiry'],'expiry').strftime('%y%m%d') or
            right != ('C' if l['right']=='call' else 'P') or
            Decimal(strike)/1000 != l['strike']):
            raise Gap('identity:contract_id_does_not_match_terms')
    cap=min(int(l['ask_size' if l['signed_qty']>0 else 'bid_size']//abs(l['signed_qty'])) for l in legs)
    if units>cap: raise Gap(f'capacity:requested_{units}_exceeds_displayed_{cap}')
    costs=candidate.get('costs',{})
    fees={k:number(costs.get(k),f'costs.{k}',0) for k in ('entry_total','expiry_total','model_exit_total')}
    required(costs,'evidence_ref')
    debit=sum((l['signed_qty']*l['multiplier']*(l['ask'] if l['signed_qty']>0 else l['bid']) for l in legs),Decimal(0))*units
    if kind=='butterfly':
        # A long 1:-2:1 fly pays between 0 and one wing width at expiry, so a natural
        # debit outside (0, width x multiplier x units) means inconsistent quotes, not an edge.
        ceiling=(ks[1]-ks[0])*legs[0]['multiplier']*units
        if debit<=0: raise Gap('butterfly:natural_debit_not_positive')
        if debit>=ceiling: raise Gap('butterfly:natural_debit_not_below_wing_width')
    return legs, units, cap, debit, fees


def expired_contracts(candidates, now):
    """Contract ids whose settlement time has passed; malformed rows are left to validate_candidate."""
    found=[]
    for c in candidates if isinstance(candidates,list) else []:
        for l in c.get('legs') or [] if isinstance(c,dict) else []:
            try:
                if isinstance(l,dict) and stamp(l.get('expiry'),'expiry')<=now: found.append(str(l.get('contract_id')))
            except Gap:
                continue
    return sorted(set(found))


def qualified_weights(payload, scenarios):
    q=payload.get('weight_qualification')
    if not q: return None, 'not_supplied_no_probability_inferred'
    try:
        if q.get('status')!='qualified' or q.get('measure')!='physical': raise Gap('weights:qualification_or_measure_invalid')
        for k in ('validator','method','validation_evidence_ref','limitations'): required(q,k)
        if stamp(q.get('frozen_at'),'weights.frozen_at')>stamp(payload['as_of'],'as_of'): raise Gap('weights:lookahead')
        if stamp(q.get('valid_until'),'weights.valid_until')<stamp(payload['as_of'],'as_of'): raise Gap('weights:expired_qualification')
        if set(q.get('scenario_ids',[]))!={s['id'] for s in scenarios}: raise Gap('weights:coverage_mismatch')
        if len({s['at'] for s in scenarios})!=1 or q.get('horizon_at')!=scenarios[0]['at']: raise Gap('weights:mixed_or_wrong_horizon')
        weights=[number(s.get('weight'),'scenario.weight',0) for s in scenarios]
        if sum(weights)!=1: raise Gap('weights:must_sum_exactly_one')
        return weights, 'conditional_on_user_supplied_external_qualification_not_independently_verified'
    except (Gap,KeyError,IndexError,TypeError,AttributeError) as e:
        return None,str(e)


def validate_option_holding(h, now, policy):
    for k in ('contract_id','underlying','expiry','right','currency','exercise_style','settlement_style',
              'settlement_session','last_trade_at','identity_evidence_ref','reference_mark_evidence_ref','exposure_evidence_ref'):
        required(h,k)
    if h['exercise_style'] not in ('american','european') or h['settlement_style'] not in ('physical','cash') or h['settlement_session'] not in ('AM','PM'):
        raise Gap('holding:unknown_exercise_or_settlement')
    if h.get('standard_deliverable_verified') is not True: raise Gap('holding:deliverable_unverified')
    exp=stamp(h['expiry'],'holding.expiry')
    if exp<=now: raise Gap('holding:already_expired')
    last_trade=stamp(h['last_trade_at'],'holding.last_trade_at')
    if last_trade>exp or now>=last_trade: raise Gap('holding:last_trade_time_invalid_or_no_longer_trading')
    qty=number(h.get('signed_qty'),'holding.signed_qty')
    strike=number(h.get('strike'),'holding.strike',0); mult=number(h.get('multiplier'),'holding.multiplier',0)
    if not qty or qty!=qty.to_integral_value() or strike<=0 or mult<=0: raise Gap('holding:invalid_quantity_or_identity')
    occ=re.fullmatch(r'([A-Z]+)\s*(\d{6})([CP])(\d{8})',h['contract_id'])
    if not occ: raise Gap('holding:standard_US_OCC_contract_id_required')
    root,day,right,k=occ.groups()
    if (root!=h.get('option_root',h['underlying']) or day!=exp.strftime('%y%m%d') or
        h['right'] not in ('call','put') or right!=('C' if h['right']=='call' else 'P') or Decimal(k)/1000!=strike):
        raise Gap('holding:contract_id_terms_mismatch')
    if h['underlying']=='SPX' and (h.get('option_root') not in ('SPX','SPXW') or mult!=100 or
        h['exercise_style']!='european' or h['settlement_style']!='cash' or h['settlement_session']!=('AM' if h.get('option_root')=='SPX' else 'PM')):
        raise Gap('holding:SPX_identity_mismatch')
    mark_at=stamp(h.get('reference_mark_asof'),'holding.reference_mark_asof')
    maxage=number(policy.get('max_quote_age_seconds'),'max_quote_age_seconds',0)
    if not 0 <= (now-mark_at).total_seconds() <= float(maxage): raise Gap('holding:reference_mark_stale_or_future')
    number(h.get('reference_mark'),'holding.reference_mark',0)
    costs=h.get('costs',{})
    for k in ('model_exit_total','expiry_total'): number(costs.get(k),'holding.costs.'+k,0)
    required(costs,'evidence_ref')


def scenario_option_mark(h, s):
    """Future explicit marks are assumptions only, never observed or calibrated prices."""
    at=stamp(s['at'],'scenario.at'); expiry=stamp(h['expiry'],'expiry')
    spot=number(s.get('prices',{}).get(h['underlying']),'holding.joint_underlying_price',0)
    if at>=expiry:
        raise Gap('mark:preexpiry_only')
    supplied=s.get('marks_by_contract',{}).get(h['contract_id'])
    if supplied is not None:
        if supplied.get('mode')!='assumption': raise Gap('scenario_mark:future_observed_mark_forbidden')
        for k in ('assumptions','evidence_ref'): required(supplied,k)
        if stamp(supplied.get('at'),'scenario_mark.at')!=at or stamp(supplied.get('expiry'),'scenario_mark.expiry')!=expiry:
            raise Gap('scenario_mark:time_or_expiry_mismatch')
        if number(supplied.get('underlying_price'),'scenario_mark.underlying_price',0)!=spot:
            raise Gap('scenario_mark:underlying_price_mismatch')
        number(supplied.get('iv'),'scenario_mark.iv',0)
        explicit_mark=number(supplied.get('mark'),'scenario_mark.mark',0)
        intrinsic=max(Decimal(0),spot-number(h['strike'],'strike') if h['right']=='call' else number(h['strike'],'strike')-spot)
        if h['exercise_style']=='american' and explicit_mark<intrinsic: raise Gap('scenario_mark:american_below_immediate_exercise_value')
        return explicit_mark, {'valuation':'explicit_scenario_mark_assumption_not_observed',
            'mark_inputs':supplied,'remaining_calendar_years_ACT365':(expiry-at).total_seconds()/(365*86400)}
    if h['exercise_style']!='european': raise Gap('holding:american_requires_explicit_scenario_mark')
    model=s.get('model',{})
    if model.get('name')!='black_scholes_european': raise Gap('holding:preexpiry_model_missing')
    for k in ('assumptions','input_evidence_ref'): required(model,k)
    years=(expiry-at).total_seconds()/(365*86400)
    # Per-underlying rates/yields avoid silently applying an index dividend input to a stock.
    terms=valuation_terms(model,h['underlying'])
    iv=number(model.get('iv_by_contract',{}).get(h['contract_id']),'holding.model.iv',0)
    mark=bs_european(spot,h['strike'],years,iv,terms.get('rate'),terms.get('dividend_yield'),h['right'])
    return Decimal(str(mark['price'])), {'valuation':'black_scholes_model_mark_not_executable',
        'remaining_calendar_years_ACT365':years,'iv':scalar(iv),'marks_and_greeks':mark}


def holding_option_pnl(h,s):
    qty=number(h['signed_qty'],'signed_qty'); mult=number(h['multiplier'],'multiplier')
    reference=number(h['reference_mark'],'reference_mark',0)
    at=stamp(s['at'],'scenario.at'); exp=stamp(h['expiry'],'holding.expiry')
    costs=h['costs']; extra={}
    if at<exp:
        mark,extra=scenario_option_mark(h,s)
        value=qty*mult*mark; fee=number(costs['model_exit_total'],'model_exit_total',0)
    else:
        spot=number(s.get('prices',{}).get(h['underlying']),'holding.joint_underlying_price',0)
        carry=Decimal(1)
        if at>exp:
            settlement=s.get('settlements_by_contract',{}).get(h['contract_id'],{})
            if settlement.get('cash_policy')!='settle_to_cash_and_carry': raise Gap('holding:post_expiry_settlement_cash_policy_missing')
            for k in ('assumptions','evidence_ref'): required(settlement,k)
            if stamp(settlement.get('at'),'settlement.at')!=exp: raise Gap('holding:settlement_time_mismatch')
            spot=number(settlement.get('underlying_price'),'settlement.underlying_price',0)
            rate=number(settlement.get('cash_carry_rate'),'settlement.cash_carry_rate')
            carry=Decimal(str(math.exp(float(rate)*(at-exp).total_seconds()/(365*86400))))
            extra['settlement_assumptions']=settlement
            if h['settlement_style']=='physical' and settlement.get('physical_delivery_policy')!='immediate_cash_equivalent_liquidation':
                raise Gap('holding:physical_delivery_liquidation_assumption_missing')
        elif h['settlement_style']=='physical':
            settlement=s.get('settlements_by_contract',{}).get(h['contract_id'],{})
            if settlement.get('physical_delivery_policy')!='immediate_cash_equivalent_liquidation':
                raise Gap('holding:physical_delivery_liquidation_assumption_missing')
            for k in ('assumptions','evidence_ref'): required(settlement,k)
            extra['settlement_assumptions']=settlement
        intrinsic=max(Decimal(0),spot-number(h['strike'],'strike') if h['right']=='call' else number(h['strike'],'strike')-spot)
        fee=Decimal(0)
        value=(qty*mult*intrinsic-number(costs['expiry_total'],'expiry_total',0))*carry
        extra['valuation']='explicit_cash_settlement_scenario'
    pnl=value-qty*mult*reference-fee
    return pnl, {'contract_id':h['contract_id'],'kind':'option','reference_value':scalar(qty*mult*reference),
                 'incremental_pnl':scalar(pnl),'historical_entry_cost_used':False,**extra}


def analyze(payload):
    result={'schema':'options_expression_lab.v1','no_order_execution':True,'hypothesis_only':True,
            'readiness':'blocked','compiler_isolated':True,'as_of':payload.get('as_of'),
            'data_mode':payload.get('data_mode'),'candidates':[],'data_gaps':[],
            'source_freshness':{'state':'clock_reported','clocks':[]},
            'probability_policy':'no_equal_weights_for_grid','hedge_claim':'not_established'}
    try:
        now=stamp(payload.get('as_of'),'as_of')
        if payload.get('data_mode') not in ('synthetic','observed'): raise Gap('data_mode:explicit_synthetic_or_observed_required')
        scenarios=payload.get('scenarios',[])
        if not isinstance(scenarios,list) or not scenarios: raise Gap('scenarios:missing')
        ids=[required(s,'id') for s in scenarios]
        if len(set(ids))!=len(ids): raise Gap('scenarios:duplicate_id')
        for s in scenarios:
            if stamp(s.get('at'),'scenario.at')<=now:
                expired=expired_contracts(payload.get('candidates'),now)
                raise Gap('contract:expired:'+','.join(expired) if expired else 'scenario:not_future')
        candidates=payload.get('candidates',[])
        if not isinstance(candidates,list) or not candidates: raise Gap('candidates:missing')
        cids=[required(c,'id') for c in candidates]
        if len(set(cids))!=len(cids): raise Gap('candidates:duplicate_id')
    except (Gap,TypeError,AttributeError) as e:
        result['data_gaps'].append(str(e)); return result
    weights,wstatus=qualified_weights(payload,scenarios)
    result['weight_status']=wstatus
    holdings=payload.get('holdings',[])
    hedge_gaps=[]; baseline=None
    if holdings:
        try:
            baseline=[]
            holding_ids=[]
            for h in holdings:
                required(h,'currency'); required(h,'exposure_evidence_ref')
                if stamp(h.get('as_of'),'holding.as_of')!=now: raise Gap('holding:as_of_mismatch')
                if h.get('kind')=='stock':
                    required(h,'symbol');number(h.get('signed_shares'),'signed_shares');number(h.get('reference_price'),'reference_price',0)
                elif h.get('kind')=='option':
                    validate_option_holding(h,now,payload.get('policy',{}));holding_ids.append(h['contract_id'])
                else: raise Gap('holdings:unsupported_kind')
            if len(holding_ids)!=len(set(holding_ids)): raise Gap('holdings:duplicate_contract_aggregate_first')
            if len({h['currency'] for h in holdings})!=1: raise Gap('holdings:mixed_currency_FX_model_missing')
            baseline_details=[]
            for scenario in scenarios:
                total=Decimal(0);details=[]
                for h in holdings:
                    if h['kind']=='stock':
                        pnl=number(h['signed_shares'],'signed_shares')*(number(scenario['prices'].get(h['symbol']),'joint_stock_price',0)-number(h['reference_price'],'reference_price',0))
                        detail={'kind':'stock','symbol':h['symbol'],'incremental_pnl':scalar(pnl)}
                    else: pnl,detail=holding_option_pnl(h,scenario)
                    total+=pnl;details.append(detail)
                baseline.append(total);baseline_details.append({'id':scenario['id'],'at':scenario['at'],'holdings':details})
            result['baseline_scenario_details']=baseline_details
        except (Gap,KeyError,TypeError,AttributeError,ValueError,OverflowError) as e:
            baseline=None;hedge_gaps.append(str(e))
        if not payload.get('related_exposure_evidence_ref'): hedge_gaps.append('related_exposure_evidence:missing')
        tags=set(t for s in scenarios for t in s.get('stress_tags',[]))
        for t in ('tail','basis_break','correlation_failure'):
            if t not in tags: hedge_gaps.append(f'joint_stress:{t}:missing')
    else: hedge_gaps.append('holdings:missing')
    if holdings and not isinstance(holdings,list):
        result['source_freshness']['state']='unknown_holding_reference_clock'
    for h in holdings if isinstance(holdings,list) else []:
        try:
            mark_at=h.get('reference_mark_asof')
            if mark_at is None: raise Gap('holding:reference_clock_missing')
            result['source_freshness']['clocks'].append(
                {'kind':'reference_mark_asof','at':mark_at,
                 'max_age_seconds':float(number(payload.get('policy',{}).get('max_quote_age_seconds'),'max_quote_age_seconds',0))})
        except (Gap,TypeError,AttributeError,ValueError,OverflowError):
            result['source_freshness']['state']='unknown_holding_reference_clock'
    result['hedge_data_gaps']=hedge_gaps
    for c in candidates:
        out={'id':c['id'],'kind':c.get('kind'),'readiness':'blocked','data_gaps':[],
             'expected_pnl':None,'probability_profit':None,'hedge_effectiveness':'not_established'}
        try:
            legs,units,cap,debit,fees=validate_candidate(c,payload)
            result['source_freshness']['clocks'].extend(
                {'kind':'quote_asof','candidate_id':c['id'],'contract_id':l['contract_id'],
                 'at':l['quote_asof'],'max_age_seconds':float(number(payload['policy']['max_quote_age_seconds'],'max_quote_age_seconds',0))} for l in legs)
            out.update({'contracts':[{k:scalar(v) for k,v in l.items()} for l in legs],
                        'units':scalar(units),'displayed_capacity_units':cap,
                        'capacity_basis':'min(side_size/abs(signed_ratio)); snapshot_only_no_fill_guarantee',
                        'natural_entry_debit':scalar(debit),'entry_cost_including_fees':scalar(debit+fees['entry_total']),
                        'costs':{k:scalar(v) for k,v in fees.items()},
                        'expiry_geometry_per_requested_units':expiry_geometry(
                            [dict(l,signed_qty=l['signed_qty']*units) for l in legs],debit+fees['entry_total']+fees['expiry_total'])})
            rows=[]; pnls=[]
            for s in scenarios:
                at=stamp(s['at'],'scenario.at'); expiry=stamp(legs[0]['expiry'],'expiry')
                spot=number(s.get('prices',{}).get(legs[0]['underlying']),'option_underlying_scenario_price',0)
                row={'id':s['id'],'at':s['at'],'stress_tags':s.get('stress_tags',[]),'terminal_or_model_spot':scalar(spot)}
                if at==expiry:
                    pnl=payoff(legs,spot)*units-debit-fees['entry_total']-fees['expiry_total']
                    row['valuation']='expiry_scenario'
                elif at>expiry: raise Gap('scenario:after_settlement_reinvestment_not_modeled')
                else:
                    model=s.get('model',{})
                    if model.get('name')!='black_scholes_european': raise Gap('pre_expiry:model_missing_use_only_expiry_scenario')
                    if legs[0]['exercise_style']!='european': raise Gap('pre_expiry:american_early_exercise_model_unsupported')
                    for k in ('assumptions','input_evidence_ref'): required(model,k)
                    terms=valuation_terms(model,legs[0]['underlying'])
                    rate=number(terms.get('rate'),'model.rate'); div=number(terms.get('dividend_yield'),'model.dividend_yield')
                    years=(expiry-at).total_seconds()/(365*86400)
                    marks=[];value=Decimal(0)
                    for l in legs:
                        iv=number(model.get('iv_by_contract',{}).get(l['contract_id']),'model.iv',0)
                        mark=bs_european(spot,l['strike'],years,iv,rate,div,l['right'])
                        value+=Decimal(str(mark['price']))*l['signed_qty']*l['multiplier']*units
                        marks.append({'contract_id':l['contract_id'],'iv':scalar(iv),'signed_qty':scalar(l['signed_qty']*units),**mark})
                    pnl=value-debit-fees['entry_total']-fees['model_exit_total']
                    row.update({'valuation':'model_mark_scenario_not_executable_exit','model':model,
                                'remaining_calendar_years_ACT365':years,'marks_and_greeks_per_option_unit':marks})
                row['option_pnl']=scalar(pnl);rows.append(row);pnls.append(pnl)
            out['scenarios']=rows;out['readiness']='hypothesis_only'
            out['hedge_data_gaps']=list(hedge_gaps)
            if weights is not None:
                out['expected_pnl']=scalar(sum((p*w for p,w in zip(pnls,weights)),Decimal(0)))
                out['probability_profit']=scalar(sum((w for p,w in zip(pnls,weights) if p>0),Decimal(0)))
            if baseline is not None and not hedge_gaps:
                if any(h['currency']!=legs[0]['currency'] for h in holdings):
                    out['hedge_data_gaps']=['hedge:currency_or_FX_model_mismatch']
                    result['candidates'].append(out)
                    continue
                joint_times=[stamp(l['quote_asof'],'quote_asof') for l in legs]+[stamp(h.get('reference_mark_asof',h['as_of']),'holding.mark_asof') for h in holdings]
                if (max(joint_times)-min(joint_times)).total_seconds()>float(number(payload['policy']['max_quote_skew_seconds'],'max_quote_skew_seconds',0)):
                    out['hedge_data_gaps']=['hedge:joint_reference_quote_time_skew']
                    result['candidates'].append(out)
                    continue
                combined=[a+b for a,b in zip(baseline,pnls)]
                for row,a,b in zip(rows,baseline,combined): row.update({'unhedged_pnl':scalar(a),'hedged_pnl':scalar(b)})
                reduction=max(Decimal(0),-min(baseline))-max(Decimal(0),-min(combined))
                upside=[(a,b) for a,b in zip(baseline,combined) if a>0]
                retention=min((b/a for a,b in upside),default=None)
                out['hedge_metrics']={'worst_loss_reduction_on_given_scenarios':scalar(reduction),
                    'minimum_upside_retention_on_positive_baseline_scenarios':scalar(retention),
                    'entry_cost':scalar(debit+fees['entry_total']),
                    'worst_unhedged_pnl':scalar(min(baseline)),'worst_hedged_pnl':scalar(min(combined))}
                out['hedge_effectiveness']='conditional_scenario_comparison_only_not_empirical_validation'
        except (Gap,KeyError,TypeError,ValueError,OverflowError,AttributeError) as e:
            out['data_gaps'].append(str(e));out['readiness']='blocked'
            out['expected_pnl']=None;out['probability_profit']=None
        result['candidates'].append(out)
    valid=[c for c in result['candidates'] if c['readiness']=='hypothesis_only']
    if valid: result['readiness']='hypothesis_only'
    # Pareto front requires complete, common-scenario risk/cost/upside dimensions.
    comparable=[c for c in valid if c.get('hedge_metrics',{}).get('minimum_upside_retention_on_positive_baseline_scenarios') is not None]
    def dimensions(c):
        m=c['hedge_metrics'];return (Decimal(m['worst_loss_reduction_on_given_scenarios']),-Decimal(m['entry_cost']),Decimal(m['minimum_upside_retention_on_positive_baseline_scenarios']))
    front=[]
    for a in comparable:
        x=dimensions(a)
        if not any(all(y>=z for y,z in zip(dimensions(b),x)) and any(y>z for y,z in zip(dimensions(b),x)) for b in comparable if a is not b): front.append(a['id'])
    result['pareto']={'candidate_ids':front,'dimensions':['maximize_scenario_worst_loss_reduction','minimize_entry_cost','maximize_minimum_upside_retention'],
                      'selection':'no_automatic_selection','gap':None if comparable else 'joint_exposure_stress_or_upside_comparison_missing'}
    return result


def inspect_chain(path):
    """Audit an existing Cboe chain WITHOUT inventing identity, timezone, or fees."""
    raw=Path(path).read_bytes();data=json.loads(raw); rows=data.get('data',{}).get('options',[])
    keys=('contract_id','underlying','expiry','right','multiplier','currency','exercise_style','settlement_style',
          'settlement_session','identity_evidence_ref','quote_asof','last_trade_at','standard_deliverable_verified')
    return {'schema':'options_expression_chain_audit.v1','no_order_execution':True,'hypothesis_only':True,
            'readiness':'blocked','path':str(Path(path).resolve()),'sha256':hashlib.sha256(raw).hexdigest(),
            'observed_envelope_timestamp':data.get('timestamp'),'row_count':len(rows),'first_observed_row':rows[0] if rows else None,
            'missing_identity_quote_fields_in_first_row':[k for k in keys if not rows or k not in rows[0]],
            'data_gaps':['historical_envelope_timestamp_not_a_fresh_timezone_qualified_leg_quote',
                         'contract_identity_and_settlement_not_verified','fee_schedule_not_supplied'],
            'note':'No envelope timestamp promoted to per-contract quote_asof. No inferred multiplier, depth or connection success.'}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    group=parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--input');group.add_argument('--inspect-chain');group.add_argument('--self-test',action='store_true')
    parser.add_argument('--output');args=parser.parse_args(argv)
    if args.self_test:
        import unittest
        suite=unittest.defaultTestLoader.discover(str(Path(__file__).parent),pattern='test_options_expression_lab.py')
        return 0 if unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful() else 1
    result=inspect_chain(args.inspect_chain) if args.inspect_chain else analyze(json.loads(Path(args.input).read_text()))
    text=json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False)
    if args.output: Path(args.output).write_text(text+'\n')
    else: print(text)
    return 2 if result['readiness']=='blocked' else 0


if __name__=='__main__': raise SystemExit(main())
