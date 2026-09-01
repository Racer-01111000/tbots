"""Fail-closed S6A runtime primitives. No population execution entrypoint exists."""
from __future__ import annotations
import bisect, hashlib, json, math, random, statistics
from dataclasses import dataclass
from pathlib import Path
from lib.ids import canonical_json, genome_id
import s6a_final as p

class S6Error(ValueError): pass
class BoundaryError(S6Error): pass
class IsolationError(S6Error): pass
class FeasibilityError(S6Error): pass

PROBE_MANIFEST_ID="s6a_probe_manifest_7a412d00a1023bfd8233c2842939a1c9b475d6b722711ab242d936c7efb1cc1e"

def _step_ok(v,r):
    return abs((v-r["min"])/r["step"]-round((v-r["min"])/r["step"]))<1e-8
def _valid_value(v,r):
    if "const" in r: return v==r["const"]
    if r["type"]=="array":
        return isinstance(v,list) and r["min_items"]<=len(v)<=r["max_items"] and (
          not r["unique"] or len(v)==len(set(v))) and all(_valid_value(x,r["items"]) for x in v)
    kind=int if r["type"]=="integer" else (int,float)
    return not isinstance(v,bool) and isinstance(v,kind) and r["min"]<=v<=r["max"] and _step_ok(v,r)

def validate_genome(code,g):
    s=p.SCHEMAS.get(code)
    if not s or not isinstance(g,dict) or set(g)!=set(s["required"]):
        raise S6Error("genome does not match exact lineage schema")
    if not all(_valid_value(g[k],r) for k,r in s["properties"].items()):
        raise S6Error("genome value outside frozen domain")
    if code=="B" and not g["exit_z"]<g["entry_z"]: raise S6Error("B constraint")
    if code=="C" and not (g["fast_ema_sessions"]<g["slow_ema_sessions"] and
      g["exit_channel_sessions"]<g["breakout_sessions"]): raise S6Error("C constraint")
    if code=="E" and not (g["fast_trend_sessions"]<g["slow_trend_sessions"] and
      g["risk_on_volatility_ratio"]<g["risk_off_volatility_ratio"]): raise S6Error("E constraint")
    if code=="F" and g["relative_strength_lookbacks"]!=sorted(g["relative_strength_lookbacks"]):
        raise S6Error("F lookbacks must increase")
    if code=="G" and (sum(g["target_weights_bps"])!=10000 or
      sum(x>0 for x in g["target_weights_bps"])<3): raise S6Error("G vector constraint")
    if genome_id(g)==p.TRADER_A: raise IsolationError("Trader A rejected")
    return genome_id(g)

def warmup(code,g):
    validate_genome(code,g)
    if code=="B": return g["lookback_sessions"]+1
    if code=="C": return max(g["slow_ema_sessions"],g["breakout_sessions"]+1,
      g["exit_channel_sessions"]+1,g["volatility_window"]+1)
    if code=="D": return g["regime_window"]+1
    if code=="E": return max(g["slow_trend_sessions"]+1,4*g["regime_volatility_window"]+1)
    if code=="F": return max(g["relative_strength_lookbacks"])+g["skip_recent_sessions"]+1
    return 0

def authorize_lane(name,*,source_path=None,start=None,end=None,feedback=None):
    if any(x is not None for x in (source_path,start,end)): raise BoundaryError("caller source/range rejected")
    lanes={"development":("2007-02-07","2018-12-31"),
      "qualification":("2019-01-01","2022-12-31"),
      "comparative":("2023-01-01","2025-12-31")}
    if name not in lanes: raise BoundaryError("unknown/2026+ lane rejected")
    if name!="development" and feedback is not None: raise BoundaryError("feedback rejected")
    if name=="comparative": raise BoundaryError("comparative requires separate future GO")
    return lanes[name]

def load_historical_bundle(name):
    """Load only an accepted, physically bounded immutable bundle; no path/range API."""
    authorize_lane(name)
    if name=="development":
        from s5a_development_bundle import load_authorized_development_bundle
        bundle=load_authorized_development_bundle()
        last="2018-12-31"
    elif name=="qualification":
        from s5b_qualification_bundle import load_authorized_qualification_bundle
        bundle=load_authorized_qualification_bundle()
        last="2022-12-31"
    else:
        raise BoundaryError("historical bundle is not execution-authorized")
    if bundle.dataset_revision!=p.DATASET or sorted(bundle.asset_set)!=sorted(p.UNIVERSE):
        raise BoundaryError("immutable bundle identity/universe mismatch")
    if any(row["timestamp"]>last for rows in bundle.per_symbol_rows.values() for row in rows):
        raise BoundaryError("post-lane observation in immutable bundle")
    return bundle

RESAMPLING_BOUND=128

@dataclass(frozen=True)
class CandidateAdmission:
    code:str
    genome_id:str
    required_price_bars:int
    minimum_available_price_bars:int
    dataset_revision:str
    episode_count:int

def development_history_availability(bundle=None):
    """Mechanically derive physical price-bar availability for every frozen
    DEVELOPMENT episode/asset. Counts include the episode's first trading
    session because each frozen warmup formula is expressed in price bars
    including the current decision session."""
    from s5a_config import EPISODE_PROTOCOL
    bundle=bundle or load_historical_bundle("development")
    lane=p.HISTORY["development"]
    if (bundle.dataset_revision!=p.DATASET or sorted(bundle.asset_set)!=sorted(p.UNIVERSE)
      or not bundle.calendar or bundle.calendar[-1]>lane["end"]
      or any(row["timestamp"]>lane["end"]
        for rows in bundle.per_symbol_rows.values() for row in rows)):
        raise BoundaryError("development feasibility source escaped immutable lane")
    episodes=EPISODE_PROTOCOL.get("episodes",[])
    if len(episodes)!=lane["episodes"]:
        raise BoundaryError("frozen development episode count changed")
    timestamps={symbol:[row["timestamp"] for row in bundle.per_symbol_rows[symbol]]
      for symbol in p.UNIVERSE}
    physical=[]
    for episode in episodes:
        start,end=episode["start_date"],episode["end_date"]
        if start<lane["start"] or end>lane["end"]:
            raise BoundaryError("development episode escaped frozen lane")
        sessions=[d for d in bundle.calendar if start<=d<=end]
        if not sessions:raise BoundaryError("development episode has no physical session")
        first=sessions[0]
        counts={symbol:bisect.bisect_right(timestamps[symbol],first)
          for symbol in p.UNIVERSE}
        physical.append({"episode_index":episode["episode_index"],
          "first_session":first,"available_price_bars":counts})
    minimums={symbol:min(row["available_price_bars"][symbol] for row in physical)
      for symbol in p.UNIVERSE}
    minimum=min(minimums.values())
    return {"schema_version":1,"dataset_revision":bundle.dataset_revision,
      "development_start":lane["start"],"development_end":lane["end"],
      "source_bundle_latest":bundle.calendar[-1],"episode_count":len(physical),
      "availability_basis":"physical immutable-bundle rows at or before each episode first trading session",
      "episodes":physical,"minimum_price_bars_by_asset":minimums,
      "minimum_price_bars":minimum,
      "latest_observation_used_for_feasibility":max(row["first_session"] for row in physical)}

def development_feasibility_summary(availability):
    binding=[]
    minimum=availability["minimum_price_bars"]
    for episode in availability["episodes"]:
        for symbol,count in episode["available_price_bars"].items():
            if count==minimum:
                binding.append({"episode_index":episode["episode_index"],
                  "first_session":episode["first_session"],"asset":symbol,
                  "available_price_bars":count})
    return {"schema_version":1,"dataset_revision":availability["dataset_revision"],
      "development_start":availability["development_start"],
      "development_end":availability["development_end"],
      "source_bundle_latest":availability["source_bundle_latest"],
      "episode_count":availability["episode_count"],
      "episode_first_sessions":[row["first_session"] for row in availability["episodes"]],
      "episode_minimum_price_bars":[min(row["available_price_bars"].values())
        for row in availability["episodes"]],
      "minimum_price_bars_by_asset":availability["minimum_price_bars_by_asset"],
      "minimum_price_bars_including_episode_start":minimum,
      "minimum_pre_episode_price_bars":minimum-1,
      "binding_points":binding,
      "latest_observation_used_for_feasibility":
        availability["latest_observation_used_for_feasibility"]}

def _resolve_availability(*,bundle=None,availability=None):
    if bundle is not None and availability is not None:
        raise BoundaryError("supply bundle or derived availability, not both")
    return availability or development_history_availability(bundle)

def new_feasibility_audit():
    return {"schema_version":1,"rejected_total":0,
      "rejected_by_role":{"founder":0,"mutated_child":0,"immigrant":0},
      "fitness_effect":None,"episodes_executed":0,"population_slots_consumed":0}

def _record_rejection(audit,role):
    if audit is None:return
    if role not in audit["rejected_by_role"]:
        raise S6Error("unknown feasibility rejection role")
    audit["rejected_total"]+=1
    audit["rejected_by_role"][role]+=1

def require_development_feasible(code,g,*,bundle=None,availability=None):
    available=_resolve_availability(bundle=bundle,availability=availability)
    gid=validate_genome(code,g);required=warmup(code,g)
    violations=[]
    for episode in available["episodes"]:
        for symbol,count in episode["available_price_bars"].items():
            if required>count:
                violations.append((episode["episode_index"],symbol,count))
    if violations:
        first=violations[0]
        raise FeasibilityError(
          f"{code} genome requires {required} price bars; physical DEVELOPMENT "
          f"availability first fails at episode {first[0]} asset {first[1]} "
          f"with {first[2]}")
    return CandidateAdmission(code,gid,required,available["minimum_price_bars"],
      available["dataset_revision"],available["episode_count"])

def with_development_feasible_candidate(code,g,action,*,bundle=None,availability=None):
    """Invoke a persistence/evaluation action only after feasibility admission."""
    admission=require_development_feasible(code,g,bundle=bundle,availability=availability)
    return action(admission)

def _candidate_is_feasible(code,g,availability,audit,role):
    try:require_development_feasible(code,g,availability=availability)
    except FeasibilityError:
        _record_rejection(audit,role)
        return False
    return True

def completion_lock_content(availability=None):
    available=availability or development_history_availability()
    return {"schema_version":2,"phase":"S6A_EXECUTABLE_PREPARATION",
      "baseline":p.BASELINE,"schema_hashes":p.SCHEMA_HASHES,"run_ids":p.RUN_IDS,
      "protocol_hashes":p.HASHES,"plan_hash":p.PLAN_HASH,
      "probe_manifest_hash":PROBE_MANIFEST_ID,
      "development_feasibility":development_feasibility_summary(available),
      "population_execution_authorized":False,"real_populations":0,
      "persisted_real_genomes":0,"historical_organism_executions":0,
      "historical_qualification_executions":0,"real_mutations":0,
      "trader_A_executions":0,"broker_connections":0,"paper_orders":0,
      "real_orders":0,"live_feeds":0,"alpaca_access":0,"external_system_access":0,
      "feasibility_rejections":0}

def assert_parent(code,parent_code,parent_gid):
    if code!=parent_code or parent_gid==p.TRADER_A: raise IsolationError("same-lineage parent required")
def assert_no_feedback(*,s5_performance=None,qualification_feedback=None):
    if s5_performance is not None or qualification_feedback is not None:
        raise BoundaryError("performance/qualification feedback rejected")

def ret(prices,L,skip=0): return prices[-1-skip]/prices[-1-skip-L]-1
def daily(prices,n): return [prices[i]/prices[i-1]-1 for i in range(len(prices)-n,len(prices))]
def vol(prices,n):
    values=daily(prices,n)
    return statistics.stdev(values) if len(values)>1 else 0
def ema(values,n):
    if len(values)<n: raise S6Error("insufficient EMA history")
    value=sum(values[:n])/n; alpha=2/(n+1)
    for x in values[n:]: value=alpha*x+(1-alpha)*value
    return value
def _cap(raw,gross,cap):
    total=sum(raw.values())
    if total<=0:return {}
    return {k:min(cap,gross*v/total) for k,v in raw.items()}
def _inverse(symbols,history,n,gross,cap):
    raw={}
    for x in symbols:
        v=vol(history[x],n)
        if v>0:raw[x]=1/v
    return _cap(raw,gross,cap)
def _cash(weights):
    out={x:round(weights.get(x,0),12) for x in p.UNIVERSE}
    out["CASH"]=round(max(0,1-sum(out.values())),12); return out

def decide_B(g,h,positions=None,holding=None):
    validate_genome("B",g); L=g["lookback_sessions"]
    rs={x:ret(h[x],L) for x in p.UNIVERSE}; m=statistics.median(rs.values())
    mad=statistics.median(abs(v-m) for v in rs.values()); scale=1.4826*mad
    z={x:(rs[x]-m)/scale if scale else 0 for x in p.UNIVERSE}
    positions=positions or set(); holding=holding or {}
    eligible=[x for x in p.UNIVERSE if z[x]<=-g["entry_z"] or
      x in positions and not (z[x]>=-g["exit_z"] or holding.get(x,0)>=g["max_holding_sessions"])]
    eligible=sorted(eligible,key=lambda x:(z[x],x))[:g["max_positions"]]
    return _cash(_inverse(eligible,h,L,g["gross_exposure_cap"],g["max_asset_weight"]))

def decide_C(g,h,positions=None):
    validate_genome("C",g); active=[]
    positions=positions or set()
    for x in p.UNIVERSE:
        values=h[x]; fast=ema(values,g["fast_ema_sessions"]); slow=ema(values,g["slow_ema_sessions"])
        high=max(values[-1-g["breakout_sessions"]:-1]); low=min(values[-1-g["exit_channel_sessions"]:-1])
        entry=fast>slow and values[-1]>high
        keep=x in positions and not (values[-1]<low or fast<=slow)
        if entry or keep: active.append((values[-1]/high-1,x))
    symbols=[x for _,x in sorted(active,key=lambda row:(-row[0],row[1]))[:g["max_positions"]]]
    return _cash(_inverse(symbols,h,g["volatility_window"],g["gross_exposure_cap"],g["max_asset_weight"]))

def _risk_basket(h,n):
    rets=[[h[x][-i]/h[x][-i-1]-1 for x in p.RISK_ASSETS] for i in range(n,0,-1)]
    idx=[1.0]
    for row in rets: idx.append(idx[-1]*(1+sum(row)/len(row)))
    return idx
def decide_D(g,h,step):
    validate_genome("D",g)
    if step%g["rebalance_sessions"]: return None
    n=g["regime_window"]; above={x:h[x][-1]>sum(h[x][-n:])/n for x in p.RISK_ASSETS}
    breadth=sum(above.values())/len(above); idx=_risk_basket(h,n); dd=idx[-1]/max(idx)-1
    symbols=p.DEFENSIVE_ASSETS if breadth<g["breadth_threshold"] or dd<=-g["risk_off_drawdown"] else p.RISK_ASSETS
    if symbols==p.DEFENSIVE_ASSETS: eligible=[x for x in symbols if ret(h[x],n)>0]
    else: eligible=[x for x in symbols if above[x]]
    eligible=sorted(eligible,key=lambda x:(-ret(h[x],n),x))[:g["selection_count"]]
    raw={x:1 for x in eligible}
    return _cash(_cap(raw,g["gross_exposure_cap"],g["max_asset_weight"]))

def decide_E(g,h,step):
    validate_genome("E",g)
    if step%g["rebalance_sessions"]: return None
    n=g["regime_volatility_window"]; basket=_risk_basket(h,4*n)
    short=statistics.stdev([basket[i]/basket[i-1]-1 for i in range(len(basket)-n,len(basket))])
    long=statistics.stdev([basket[i]/basket[i-1]-1 for i in range(1,len(basket))])
    if long==0 and short!=0:
        state="risk_off"
    else:
        ratio=1.0 if long==0 else short/long
        state="risk_on" if ratio<g["risk_on_volatility_ratio"] else (
          "risk_off" if ratio>=g["risk_off_volatility_ratio"] else "neutral")
    trend={x:(ret(h[x],g["fast_trend_sessions"])+ret(h[x],g["slow_trend_sessions"]))/2 for x in p.UNIVERSE}
    if state=="risk_on": eligible=[x for x in p.UNIVERSE if trend[x]>0]
    elif state=="neutral": eligible=[x for x in p.UNIVERSE if ret(h[x],g["slow_trend_sessions"])>0]
    else: eligible=[x for x in p.DEFENSIVE_ASSETS if ret(h[x],g["slow_trend_sessions"])>0]
    eligible=sorted(eligible,key=lambda x:(-trend[x],x))[:g["top_k"]]
    gross=g["gross_exposure_cap"]*{"risk_on":1,"neutral":.5,"risk_off":.25}[state]
    return _cash(_inverse(eligible,h,n,gross,g["max_asset_weight"]))

def _average_ranks(values):
    ordered=sorted(values,key=lambda x:(x[1],x[0])); result={}
    i=0
    while i<len(ordered):
        j=i
        while j+1<len(ordered) and ordered[j+1][1]==ordered[i][1]:j+=1
        rank=(i+j)/2/(len(ordered)-1) if len(ordered)>1 else 1
        for k in range(i,j+1):result[ordered[k][0]]=rank
        i=j+1
    return result
def decide_F(g,h,step):
    validate_genome("F",g)
    if step%g["rebalance_sessions"]: return None
    horizon=[]; rawmeans={x:[] for x in p.UNIVERSE}
    for L in g["relative_strength_lookbacks"]:
        vals=[(x,ret(h[x],L,g["skip_recent_sessions"])) for x in p.UNIVERSE]
        horizon.append(_average_ranks(vals))
        for x,v in vals:rawmeans[x].append(v)
    score={x:sum(r[x] for r in horizon)/len(horizon) for x in p.UNIVERSE}
    breadth=sum(statistics.mean(rawmeans[x])>0 for x in p.UNIVERSE)/len(p.UNIVERSE)
    if breadth<g["breadth_threshold"]:return _cash({})
    selected=sorted(p.UNIVERSE,key=lambda x:(-score[x],x))[:g["top_k"]]
    raw={x:len(selected)-i for i,x in enumerate(selected)}
    return _cash(_cap(raw,g["gross_exposure_cap"],g["max_asset_weight"]))

def decide_G(g,current,holding,step,initial=False):
    validate_genome("G",g)
    if step%g["review_interval_sessions"] and not initial:return None
    target={x:g["target_weights_bps"][i]/10000 for i,x in enumerate(p.UNIVERSE)}
    desired={}
    band=g["drift_band_bps"]/10000
    for x in p.UNIVERSE:
        drift=current.get(x,0)-target[x]
        if abs(drift)>=band and (initial or holding.get(x,0)>=g["minimum_hold_sessions"]):
            desired[x]=-drift
    gross=sum(abs(v) for v in desired.values()); budget=g["turnover_budget_bps"]/10000
    if gross>budget:desired={x:v*budget/gross for x,v in desired.items()}
    new={x:max(0,current.get(x,0)+desired.get(x,0)) for x in p.UNIVERSE}
    return _cash(new)

@dataclass(frozen=True)
class HaltDirective:
    halted:bool; cancel_pending_entries:bool; liquidate_next_open:bool; remain_cash:bool
def halt(drawdown,already=False):
    triggered=already or drawdown<=-p.DRAWDOWN_HALT
    return HaltDirective(triggered,triggered,triggered,triggered)
def fill_price(raw_open,side):
    return raw_open*(1.0005 if side=="buy" else .9995)
def fill_cost(notional): return round(abs(notional)*.0005)

def derive_seed(code,*parts):
    if code not in p.SEEDS:raise IsolationError("unknown seed stream")
    digest=hashlib.sha256(canonical_json([p.SEEDS[code],*parts]).encode()).digest()
    return int.from_bytes(digest[:8],"big")&((1<<63)-1)
def _draw(rng,r):
    count=round((r["max"]-r["min"])/r["step"])
    return r["min"]+rng.randrange(count+1)*r["step"]

def mutate(code,parent,seed):
    validate_genome(code,parent); rng=random.Random(seed); s=p.SCHEMAS[code]
    mutable=[k for k,r in s["properties"].items() if "const" not in r]
    for _ in range(128):
        g={k:(list(v) if isinstance(v,list) else v) for k,v in parent.items()}
        selected=[k for k in mutable if rng.random()<.25]
        if not selected:selected=[rng.choice(mutable)]
        for k in selected:
            r=s["properties"][k]
            if code=="F" and k=="relative_strength_lookbacks":
                ops=["shift"]+(["add"] if len(g[k])<4 else [])+(["remove"] if len(g[k])>2 else [])
                op=rng.choice(ops)
                if op=="remove":g[k].pop(rng.randrange(len(g[k])))
                elif op=="add":
                    choices=[x for x in range(20,253) if x not in g[k]];g[k].append(rng.choice(choices))
                else:
                    i=rng.randrange(len(g[k])); choices=[d for d in range(-24,25) if d and 20<=g[k][i]+d<=252 and g[k][i]+d not in g[k]]
                    if choices:g[k][i]+=rng.choice(choices)
                g[k].sort()
            elif code=="G" and k=="target_weights_bps":
                donors=[i for i,x in enumerate(g[k]) if x>0];d=rng.choice(donors)
                recipients=[i for i,x in enumerate(g[k]) if i!=d and x<4000];q=rng.choice(recipients)
                maxu=min(g[k][d],4000-g[k][q])//100
                amount=rng.randint(1,maxu)*100;g[k][d]-=amount;g[k][q]+=amount
            else:
                span=round((r["max"]-r["min"])/r["step"]);delta=rng.choice([x for x in range(-math.ceil(.1*span),math.ceil(.1*span)+1) if x])
                idx=round((g[k]-r["min"])/r["step"]);idx=max(0,min(span,idx+delta));g[k]=r["min"]+idx*r["step"]
        try:validate_genome(code,g)
        except S6Error:continue
        if g!=parent:return g
    raise S6Error("mutation failed closed after 128 attempts")

def tournament(rows,seed):
    if len(rows)<4:raise S6Error("tournament needs four")
    sample=random.Random(seed).sample(rows,4)
    return sorted(sample,key=lambda r:(-r["fitness"],r["genome_id"]))[0]
def build_generation(code,current,generation,*,bundle=None,availability=None,audit=None):
    available=_resolve_availability(bundle=bundle,availability=availability)
    if not 1<=generation<=12 or len(current)!=64:raise S6Error("invalid generation input")
    for row in current:
        if row.get("lineage",code)!=code or validate_genome(code,row["genome"])!=row["genome_id"]:
            raise IsolationError("current population is not exact same-lineage input")
        require_development_feasible(code,row["genome"],availability=available)
    ranked=sorted(current,key=lambda r:(-r["fitness"],r["genome_id"]))
    elites=[{"role":"elite","lineage":code,"genome":row["genome"],"genome_id":row["genome_id"],
      "parent_genome_id":row["genome_id"]} for row in ranked[:8]]
    children=[]
    for slot in range(48):
        parent_seed=derive_seed(code,"child",generation,slot)
        parent=tournament(current,parent_seed)
        assert_parent(code,parent.get("lineage",code),parent["genome_id"])
        for attempt in range(RESAMPLING_BOUND):
            seed=derive_seed(code,"child",generation,slot,"feasibility",attempt)
            child=mutate(code,parent["genome"],seed)
            if _candidate_is_feasible(code,child,available,audit,"mutated_child"):break
        else:raise S6Error("feasible child generation failed closed after 128 attempts")
        children.append({"role":"child","lineage":code,"genome":child,"genome_id":genome_id(child),
          "parent_genome_id":parent["genome_id"]})
    immigrants=[]
    for slot in range(8):
        for attempt in range(RESAMPLING_BOUND):
            seed=derive_seed(code,"immigrant",generation,slot,"feasibility",attempt)
            g=founder(code,seed)
            if _candidate_is_feasible(code,g,available,audit,"immigrant"):break
        else:raise S6Error("feasible immigrant generation failed closed after 128 attempts")
        immigrants.append({"role":"immigrant","lineage":code,"genome":g,"genome_id":genome_id(g),
          "parent_genome_id":None})
    result=elites+children+immigrants
    if any(validate_genome(code,row["genome"])!=row["genome_id"] for row in result):
        raise S6Error("generation validation failed")
    for row in result:require_development_feasible(code,row["genome"],availability=available)
    return result
def rank_development(rows):
    if len(rows)!=64 or any(row.get("generation")!=12 or not row.get("verified") for row in rows):
        raise S6Error("all 64 Gen12 slots require independent verification")
    unique={}
    for row in rows:
        old=unique.get(row["genome_id"])
        if old is None or row["fitness"]>old["fitness"]:unique[row["genome_id"]]=row
    if len(unique)<8:raise S6Error("fewer than eight unique verified Gen12 genomes")
    return sorted(unique.values(),key=lambda row:(-row["fitness"],row["genome_id"]))[:8]
def rank_qualification(rows):
    return sorted(rows,key=lambda r:(-r["fitness"],r["genome_id"]))

def similarity(a,b):
    if set(a)!=set(p.DIVERSITY["probe_ids"]) or set(b)!=set(a):raise S6Error("probe mismatch")
    ws=[];acts=[]
    for probe in p.DIVERSITY["probe_ids"]:
        if len(a[probe]["weights"])!=len(b[probe]["weights"]):raise S6Error("signature alignment")
        for av,bv in zip(a[probe]["weights"],b[probe]["weights"]):
            ws.append(1-.5*sum(abs(av[x]-bv[x]) for x in p.UNIVERSE+["CASH"]))
            aa={x for x,v in av.items() if v>0};bb={x for x,v in bv.items() if v>0}
            acts.append(len(aa&bb)/len(aa|bb) if aa|bb else 1)
    w=round(statistics.mean(ws),12);active=round(statistics.mean(acts),12)
    ta=sum(a[x]["turnover"] for x in p.DIVERSITY["probe_ids"])
    tb=sum(b[x]["turnover"] for x in p.DIVERSITY["probe_ids"])
    turn=round(1-min(1,abs(ta-tb)),12)
    return round(.60*w+.25*active+.15*turn,12)
def select_winners(qualified,signatures,a_signature):
    winners={}
    for code in p.ADVANCEMENT["selection_order"]:
        for row in rank_qualification(qualified[code]):
            sig=signatures[row["genome_id"]]
            if round(similarity(sig,a_signature),12)>0.750000000000:continue
            if any(w and round(similarity(sig,signatures[w["genome_id"]]),12)>0.800000000000
              for w in winners.values()):continue
            winners[code]=row;break
        if code not in winners:winners[code]=None
    return winners
def reproduce(function,*args,**kwargs):
    a=function(*args,**kwargs);b=function(*args,**kwargs)
    if canonical_json(a)!=canonical_json(b):raise S6Error("independent reproduction disagreed")
    return a

def founder(code,seed):
    rng=random.Random(seed);schema=p.SCHEMAS[code]
    for attempt in range(128):
        g={}
        for name,rule in schema["properties"].items():
            if "const" in rule:
                g[name]=list(rule["const"]) if isinstance(rule["const"],list) else rule["const"]
            elif rule["type"]!="array":
                g[name]=_draw(rng,rule)
            elif code=="F":
                g[name]=sorted(rng.sample(range(20,253),rng.randint(2,4)))
            else:
                units=[0]*8
                for unused in range(100):
                    units[rng.choice([i for i,x in enumerate(units) if x<40])]+=1
                g[name]=[x*100 for x in units]
        try:validate_genome(code,g)
        except S6Error:continue
        return g
    raise S6Error("founder generation failed closed after 128 attempts")

def build_gen0(code,*,bundle=None,availability=None,audit=None):
    available=_resolve_availability(bundle=bundle,availability=availability)
    result=[];seen=set()
    for slot in range(64):
        for attempt in range(RESAMPLING_BOUND):
            g=founder(code,derive_seed(code,"founder",slot,attempt));gid=genome_id(g)
            if gid in seen:continue
            if not _candidate_is_feasible(code,g,available,audit,"founder"):continue
            break
        else:raise S6Error("unique feasible Gen0 founder generation failed closed")
        seen.add(gid)
        result.append({"role":"founder","lineage":code,"genome":g,"genome_id":gid,
          "parent_genome_id":None})
    if len(result)!=64 or len(seen)!=64:raise S6Error("Gen0 must contain 64 unique feasible founders")
    return result

def validate_envelope(path,identity):
    envelope=json.loads(Path(path).read_text())
    prefix=identity.rsplit("_",1)[0]+"_"
    if (set(envelope)!={"content","manifest_hash"} or
      envelope["manifest_hash"]!=identity or p.h(prefix,envelope["content"])!=identity or
      identity not in Path(path).name):
        raise S6Error("forged or misplaced immutable manifest")
    return envelope["content"]

def load_synthetic_probes():
    lock_path=p.PROTOCOL_DIR/"s6a_executable_preparation_lock.json"
    lock=json.loads(lock_path.read_text())
    expected=completion_lock_content()
    lock_id=p.h("s6a_completion_lock_",expected)
    if (set(lock)!={"content","manifest_hash"} or lock["manifest_hash"]!=lock_id or
      lock["content"]!=expected or
      lock["content"].get("probe_manifest_hash")!=PROBE_MANIFEST_ID or
      lock["content"].get("population_execution_authorized") is not False):
        raise S6Error("preparation lock mismatch")
    manifest_path=p.PROTOCOL_DIR/f"synthetic_probe_manifest_{PROBE_MANIFEST_ID}.json"
    manifest=validate_envelope(manifest_path,PROBE_MANIFEST_ID)
    if (manifest.get("historical_rows")!=0 or manifest.get("sessions_per_probe")!=420 or
      set(manifest.get("probes",{}))!=set(p.DIVERSITY["probe_ids"])):
        raise S6Error("synthetic probe manifest contract mismatch")
    probes={}
    for name,row in manifest["probes"].items():
        rel=Path(row["path"])
        expected_parent=(p.ROOT/"data"/"s6a_synthetic_probes").resolve()
        path=(p.ROOT/rel).resolve()
        if path.parent!=expected_parent or path.name!=f'{row["identity"]}.json':
            raise S6Error("synthetic probe path escaped fixed root")
        payload=path.read_bytes()
        if hashlib.sha256(payload).hexdigest()!=row["file_sha256"]:
            raise S6Error("synthetic probe file hash mismatch")
        content=validate_envelope(path,row["identity"])
        if (content.get("probe_id")!=name or len(content.get("rows",[]))!=420 or
          any(set(item)!={"synthetic_session","assets"} or set(item["assets"])!=set(p.UNIVERSE)
            for item in content["rows"])):
            raise S6Error("synthetic probe content mismatch")
        probes[name]=content
    return probes

def compute_fitness(rows,expected_count,*,admission,s5_performance=None,qualification_feedback=None):
    if not isinstance(admission,CandidateAdmission):
        raise FeasibilityError("fitness requires prior development-feasibility admission")
    assert_no_feedback(s5_performance=s5_performance,
      qualification_feedback=qualification_feedback)
    if len(rows)!=expected_count:raise S6Error("complete frozen episode set required")
    returns=[x["total_return"] for x in rows];sharpes=[x["sharpe"] for x in rows]
    median_return=statistics.median(returns);median_sharpe=statistics.median(sharpes)
    dispersion=statistics.median(abs(x-median_return) for x in returns)
    consistency=.5*sum(x>0 for x in returns)/len(returns)+.5*(1-min(1,dispersion/.10))
    worst=min(x["max_drawdown"] for x in rows)
    halt_rate=sum(bool(x["halted"]) for x in rows)/len(rows)
    turnover=statistics.median(x["turnover"] for x in rows)
    costs=statistics.median(x["transaction_cost_rate"] for x in rows)
    absolute=[abs(x) for x in returns];total=sum(absolute)
    if total==0 or len(rows)<2:concentration=0
    else:
        hhi=sum((x/total)**2 for x in absolute);floor=1/len(rows)
        concentration=max(0,min(1,(hhi-floor)/(1-floor)))
    value=(3*median_return+.2*median_sharpe-2*abs(worst)+.5*consistency
      -.25*halt_rate-.02*turnover-5*costs-.25*concentration)
    return {"fitness":round(value,12),"median_episode_return":round(median_return,12),
      "median_episode_sharpe":round(median_sharpe,12),"worst_drawdown":round(worst,12),
      "consistency_score":round(consistency,12),"halt_rate":round(halt_rate,12),
      "median_episode_turnover":round(turnover,12),
      "median_transaction_cost_rate":round(costs,12),
      "performance_concentration":round(concentration,12)}
