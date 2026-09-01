"""Frozen executable S6A protocols. Importing this module never executes a trader."""
from __future__ import annotations
import hashlib, json
from pathlib import Path
from lib.ids import canonical_json

ROOT=Path(__file__).resolve().parents[1]
PROTOCOL_DIR=ROOT/"evolution"/"protocol"
PROBE_DIR=ROOT/"data"/"s6a_synthetic_probes"
BASELINE="657bbd7c0cc00adb5b479bafa0af2e1b130201fa"
DATASET="ds_7e16896c873671fe86ac416b24a0ce74502249a8a0fc33603e0f1935e5fab131"
TRADER_A="gen_0307d23c13fd796db749e78c86947c04ac7de020b3e4c6f02ea1f95dc10e0155"
UNIVERSE=["SPY","EFA","EEM","IEF","TLT","GLD","DBC","VNQ"]
RISK_ASSETS=["SPY","EFA","EEM","DBC","VNQ"]
DEFENSIVE_ASSETS=["IEF","TLT","GLD"]
NAMES={"B":"Mean Reversion","C":"Faster Trend","D":"Defensive Rotation",
       "E":"Hybrid Trend/Regime","F":"Relative Strength / Cross-Sectional Rotation",
       "G":"Low-Turnover Allocation"}
SEEDS={c:2026082901+i for i,c in enumerate(NAMES,1)}
DRAWDOWN_HALT=0.12

def h(prefix,value):
    return prefix+hashlib.sha256(canonical_json(value).encode()).hexdigest()
def integer(lo,hi,step=1): return {"type":"integer","min":lo,"max":hi,"step":step}
def number(lo,hi,step): return {"type":"number","min":lo,"max":hi,"step":step}
def const(value): return {"const":value}
def array(items,lo,hi,unique=False): return {"type":"array","items":items,"min_items":lo,"max_items":hi,"unique":unique}
def schema(code,family,signal,allocation,mutable,constraints=()):
    fixed={"strategy_family":const(family),"signal_model":const(signal),
           "allocation_rule":const(allocation),"universe":const(UNIVERSE),
           "direction":const("long_only"),"leverage":const("none"),"shorting":const("none")}
    props={**fixed,**mutable}
    return {"schema_version":2,"phase":"S6A_EXECUTABLE_PREPARATION","lineage":code,
            "name":NAMES[code],"additional_properties":False,"required":list(props),
            "properties":props,"constraints":list(constraints),
            "genome_id_rule":"gen_ + sha256(canonical_json(genome))",
            "external_drawdown_halt_pct":DRAWDOWN_HALT,
            "trader_A_allowed":False,"cross_lineage_parent_allowed":False}

SCHEMAS={
"B":schema("B","mean_reversion","cross_asset_robust_zscore","inverse_volatility_contrarian",{
 "lookback_sessions":integer(5,60),"entry_z":number(.75,3,.05),"exit_z":number(0,1,.05),
 "max_holding_sessions":integer(2,40),"max_positions":integer(1,4),
 "max_asset_weight":number(.10,.35,.01),"gross_exposure_cap":number(.25,1,.01)},
 ("exit_z < entry_z",)),
"C":schema("C","fast_trend_breakout","dual_ema_channel_breakout","inverse_volatility_breakout",{
 "fast_ema_sessions":integer(3,20),"slow_ema_sessions":integer(21,100),
 "breakout_sessions":integer(10,90),"exit_channel_sessions":integer(5,45),
 "volatility_window":integer(10,40),"max_positions":integer(1,4),
 "max_asset_weight":number(.10,.40,.01),"gross_exposure_cap":number(.30,1,.01)},
 ("fast_ema_sessions < slow_ema_sessions","exit_channel_sessions < breakout_sessions")),
"D":schema("D","defensive_rotation","risk_regime_drawdown_breadth","equal_weight_rotation",{
 "regime_window":integer(40,250),"breadth_threshold":number(.20,.80,.05),
 "risk_off_drawdown":number(.03,.15,.01),"selection_count":integer(1,3),
 "rebalance_sessions":integer(10,65),"max_asset_weight":number(.20,.70,.01),
 "gross_exposure_cap":number(.20,1,.01)},()),
"E":schema("E","hybrid_trend_regime","three_state_volatility_gated_trend","state_inverse_volatility",{
 "fast_trend_sessions":integer(20,90),"slow_trend_sessions":integer(100,300),
 "regime_volatility_window":integer(20,90),"risk_on_volatility_ratio":number(.50,1.25,.05),
 "risk_off_volatility_ratio":number(1.30,3,.05),"top_k":integer(1,4),
 "rebalance_sessions":integer(5,40),"max_asset_weight":number(.10,.40,.01),
 "gross_exposure_cap":number(.25,1,.01)},
 ("fast_trend_sessions < slow_trend_sessions",
  "risk_on_volatility_ratio < risk_off_volatility_ratio")),
"F":schema("F","relative_strength_rotation","cross_sectional_multi_horizon_rank","rank_points",{
 "relative_strength_lookbacks":array(integer(20,252),2,4,True),
 "skip_recent_sessions":integer(0,20),"top_k":integer(1,4),
 "breadth_threshold":number(.20,.80,.05),"rebalance_sessions":integer(10,65),
 "max_asset_weight":number(.10,.50,.01),"gross_exposure_cap":number(.25,1,.01)},
 ("relative_strength_lookbacks strictly increasing",)),
"G":schema("G","low_turnover_allocation","strategic_weight_drift_band","budgeted_banded_rebalance",{
 "target_weights_bps":array(integer(0,4000,100),8,8,False),
 "drift_band_bps":integer(100,1500,50),"minimum_hold_sessions":integer(20,180,5),
 "review_interval_sessions":integer(20,130,5),"turnover_budget_bps":integer(100,3000,50)},
 ("sum(target_weights_bps) == 10000","count(nonzero target_weights_bps) >= 3"))}

EXECUTION={
 "schema_version":2,"phase":"S6A_EXECUTABLE_PREPARATION","universe":UNIVERSE,
 "direction":"long_only","leverage":"none","shorting":"none",
 "signal_price":"adjusted close through session t","decision_time":"after session t close",
 "fill_time":"session t+1 raw open","commission_bps_per_fill":5,
 "adverse_slippage_bps_per_fill":5,"starting_cash_cents":100000000,
 "asset_tie_break":"ticker ascending","final_session_pending_orders":"cancel_unfilled",
 "drawdown":{"threshold":.12,"mutable":False,"comparison":"drawdown <= -0.12",
  "mark_price":"session raw close","actions":["cancel pending entries",
  "liquidate at next permitted open","remain cash for episode","no episode reset"]}}
OPERATORS={
 "B":{"formulae":["r_i=P_i[t]/P_i[t-L]-1","m=median(r_i)",
 "MAD=median(abs(r_i-m))","scale=1.4826*MAD","z_i=0 if scale=0 else (r_i-m)/scale"],
 "entry":"z_i <= -entry_z","exit":"z_i >= -exit_z or holding_sessions >= max_holding_sessions",
 "rank":"z ascending then ticker ascending","schedule":"every eligible session",
 "sizing":"inverse sample volatility ddof=1 over L daily returns; zero vol ineligible; cap once; residual cash",
 "cash":"no eligible asset","warmup":"lookback_sessions+1","maximum_warmup":61},
 "C":{"ema":"alpha=2/(n+1); seed SMA first n closes; recursive thereafter",
 "entry":"fast_EMA > slow_EMA and close > max(previous breakout_sessions closes)",
 "exit":"close < min(previous exit_channel_sessions closes) or fast_EMA <= slow_EMA",
 "rank":"breakout strength descending then ticker ascending","schedule":"every session",
 "sizing":"inverse sample volatility ddof=1; cap once; residual cash",
 "cash":"no valid active signal",
 "warmup":"max(slow_ema_sessions,breakout_sessions+1,exit_channel_sessions+1,volatility_window+1)",
 "maximum_warmup":100},
 "D":{"breadth":"fraction risk assets close > regime_window SMA",
 "basket":"daily-rebalanced equal-weight risk-asset return index; initialize 1.0 before first window return",
 "drawdown":"current basket index / maximum index over regime_window+1 index levels - 1",
 "risk_off":"breadth < threshold or basket_drawdown <= -risk_off_drawdown",
 "risk_on":"eligible risk assets above SMA; rank regime-window return descending then ticker",
 "risk_off_selection":"defensive assets with positive regime-window return; rank descending then ticker",
 "sizing":"equal weight selected; cap once; residual cash","cash":"no eligible asset",
 "schedule":"episode session 0 then every rebalance_sessions","warmup":"regime_window+1",
 "maximum_warmup":251},
 "E":{"trend":"mean(fast-horizon total return,slow-horizon total return)",
 "basket":"daily-rebalanced equal-weight returns of D risk assets",
 "ratio":"sample vol last n returns / sample vol last 4n returns; both zero=>1; long only zero=>risk_off",
 "states":"risk_on if ratio < on; risk_off if ratio >= off; neutral otherwise",
 "multipliers":{"risk_on":1.0,"neutral":.5,"risk_off":.25},
 "eligibility":{"risk_on":"all positive trend","neutral":"all positive slow return",
 "risk_off":"IEF,TLT,GLD with positive slow return"},
 "rank":"trend descending then ticker","sizing":"inverse sample volatility over n; cap once; residual cash",
 "cash":"no eligible asset","schedule":"episode session 0 then every rebalance_sessions",
 "warmup":"max(slow_trend_sessions+1,4*regime_volatility_window+1)","maximum_warmup":361},
 "F":{"return":"R_i(L,S)=P_i[t-S]/P_i[t-S-L]-1",
 "rank":"average ranks for ties; normalize rank to [0,1]; mean across horizons",
 "breadth":"fraction assets with positive arithmetic mean raw horizon return",
 "cash":"breadth < threshold or no eligible asset",
 "selection":"score descending then ticker; top_k","sizing":"rank points k..1; cap once; residual cash",
 "schedule":"episode session 0 then every rebalance_sessions",
 "warmup":"max(lookbacks)+skip_recent_sessions+1","maximum_warmup":273},
 "G":{"forecast":"none","schedule":"episode session 0 then every review_interval_sessions",
 "actual_weight_price":"session t raw close","drift":"actual_weight-target_weight",
 "eligible":"abs(drift)>=band and minimum hold met; initial allocation exempt",
 "budget":"maximum gross desired notional per review/current equity; no carry",
 "scaling":"scale all desired trades proportionally if over budget",
 "execution":"sells before buys; proportionally reduce buys for cash/costs; halt overrides",
 "warmup":"0 pre-episode bars","maximum_warmup":0}}

HISTORY={"schema_version":2,"dataset_revision":DATASET,
 "development":{"start":"2007-02-07","end":"2018-12-31","episodes":12},
 "qualification":{"episodes":[["2019-01-01","2019-12-31"],["2020-01-01","2020-12-31"],
 ["2021-01-01","2021-12-31"],["2022-01-01","2022-12-31"]],"feedback":False},
 "comparative":{"start":"2023-01-01","end":"2025-12-31",
 "label":"PREVIOUSLY_OBSERVED_HISTORICAL_COMPARATIVE_REPORTING","requires_future_GO":True,
 "may_change":[]},
 "prohibited":{"start":"2026-01-01","end":"OPEN",
 "uses":["evolution","qualification","similarity","comparative_reporting"]},
 "prospective_clean_evidence":"only after winners and forward protocol frozen",
 "caller_supplied_source_or_range":False}
FITNESS={"schema_version":2,"episodes":"complete frozen lane episodes",
 "formula":"3.0*median_episode_return + 0.20*median_episode_sharpe - 2.0*abs(worst_drawdown) + 0.50*consistency_score - 0.25*halt_rate - 0.02*median_episode_turnover - 5.0*median_transaction_cost_rate - 0.25*performance_concentration",
 "round_places":12,"tie_break":"fitness descending then genome_id ascending",
 "cross_lineage_comparison":False}
DEVELOPMENT_FEASIBILITY={"schema_version":1,
 "scope":["founder","mutated_child","immigrant"],
 "required_price_bars":"frozen lineage warmup(code, genome), including the episode's first trading session",
 "availability":"for every frozen DEVELOPMENT episode and every frozen-universe asset, count only physically present immutable-bundle price rows at or before that episode's first trading session",
 "acceptance":"required_price_bars <= available_price_bars for every DEVELOPMENT episode and every frozen-universe asset",
 "enforcement_before":["persistence","fitness","episode_execution"],
 "rejection":{"fitness":None,"episodes_executed":0,"population_slot_consumed":False,
   "action":"deterministically reject and resample","resampling_bound":128,
   "audit_counts_may_affect_fitness":False},
 "prohibited":["fabrication","interpolation","backfill","shortened_indicators",
   "asset_omission","development_date_change","2019_or_later_observation"],
 "latest_permitted_observation":"2018-12-31"}
EVOLUTION={"schema_version":2,"gen0_founders":64,"transitions":12,"last_generation":12,
 "per_transition":{"elites":8,"children":48,"immigrants":8},"slots_per_lineage":832,
 "slots_total":4992,"crossover":False,"tournament":{"size":4,"replacement":False,
 "winner":"fitness descending then genome_id ascending","parents":"current lineage only"},
 "scalar_mutation":{"field_probability":.25,"force_one_if_none":True,
 "domain":"integer step index","delta":"uniform nonzero integer in +/-ceil(10% domain steps)",
 "clip":True,"unchanged_child":"reject and resample complete mutation draw","retry_limit":128},
 "F_array":{"operation":"uniform among currently valid shift/add/remove",
 "shift":"uniform index then uniform nonzero legal step delta within 10% horizon span",
 "add":"uniform legal unused horizon","remove":"uniform index when length>2"},
 "G_vector":{"donor":"uniform among positive targets","recipient":"uniform distinct asset below cap",
 "amount":"uniform legal positive multiple of 100 bps","constraints":"sum 10000, each<=4000, >=3 nonzero"},
 "immigrants":"uniform step-index draws; F length uniform 2..4 then unused horizons; G sequential uniform legal 100-bps unit composition; reject invalid; 128 retries",
 "seed_streams":"lineage-specific SHA-256 derivation","cross_lineage":False,
 "development_feasibility":DEVELOPMENT_FEASIBILITY}
ADVANCEMENT={"schema_version":2,"development_source":"Gen12 only",
 "independent_verification":"all Gen12 slots before ranking",
 "development_advance":{"count":8,"unique_genomes":True,"threshold":None,
 "rank":"fitness descending then genome_id ascending"},
 "qualification":{"episodes":4,"fitness":"same frozen equation with N=4",
 "feedback":False,"rank":"fitness descending then genome_id ascending"},
 "selection_order":["B","C","D","E","F","G"],
 "winner_rule":"first qualification-ranked candidate with A similarity<=0.75 and all prior winners<=0.80",
 "breach":"skip unchanged candidate","all_eight_breach":"NO_FROZEN_WINNER",
 "regeneration":False,"threshold_changes":False}
DIVERSITY={"schema_version":2,"probe_ids":["shock_then_reversal","persistent_uptrend",
 "broad_risk_off","regime_transition","cross_sectional_dispersion","quiet_allocation_drift"],
 "sessions_per_probe":420,"historical_rows":False,
 "signature":"per eligible post-warmup session target-weight vector over universe+CASH; align comparisons at later candidate warmup",
 "weight_similarity":"per-session 1-0.5*L1 then mean sessions",
 "active_similarity":"per-session Jaccard positive targets then mean sessions",
 "turnover_similarity":"1-min(1,abs(total_probe_turnover_a-total_probe_turnover_b))",
 "composite":"round12(0.60*round12(weight)+0.25*round12(active)+0.15*round12(turnover))",
 "A_max":.75,"pairwise_max":.80,"equality_permitted":True,
 "breach":"strictly greater","selection_order":["B","C","D","E","F","G"],
 "rounding":"IEEE-754 round-to-nearest ties-to-even at 12 decimal places",
 "synthetic_A_signature":"permitted reference computation, not historical Trader A execution"}

HASHES={name:h(f"s6a_{name}_",value) for name,value in {
 "execution":EXECUTION,"operators":OPERATORS,"history":HISTORY,"fitness":FITNESS,
 "evolution":EVOLUTION,"advancement":ADVANCEMENT,"diversity":DIVERSITY}.items()}
SCHEMA_HASHES={c:h(f"s6a_schema_{c.lower()}_",v) for c,v in SCHEMAS.items()}
def run_id(c):
    return h(f"s6a_{c.lower()}_",{"baseline":BASELINE,"seed":SEEDS[c],
      "schema":SCHEMA_HASHES[c],"protocols":HASHES})
RUN_IDS={c:run_id(c) for c in NAMES}
PLAN={"schema_version":2,"baseline":BASELINE,"seeds":SEEDS,"run_ids":RUN_IDS,
 "schema_hashes":SCHEMA_HASHES,"protocol_hashes":HASHES,
 "status":"EXECUTABLE_PROTOCOL_PREPARED_POPULATION_EXECUTION_LOCKED",
 "population_execution_authorized":False,"real_population_rows":0,
 "persisted_real_genomes":0,"historical_executions":0}
PLAN_HASH=h("s6a_plan_",PLAN)

def envelope(value,identity):
    return (json.dumps({"content":value,"manifest_hash":identity},sort_keys=True,indent=2)+"\n").encode()
def artifact_map(probe_manifest=None):
    result={}
    for name,value in [("execution",EXECUTION),("operators",OPERATORS),("history",HISTORY),
      ("fitness",FITNESS),("evolution",EVOLUTION),("advancement",ADVANCEMENT),
      ("diversity",DIVERSITY)]:
        identity=HASHES[name]; result[PROTOCOL_DIR/f"{name}_{identity}.json"]=(value,identity)
    for c,value in SCHEMAS.items():
        identity=SCHEMA_HASHES[c]
        result[PROTOCOL_DIR/f"genome_schema_{identity}.json"]=(value,identity)
    result[PROTOCOL_DIR/f"run_plan_{PLAN_HASH}.json"]=(PLAN,PLAN_HASH)
    if probe_manifest:
        identity=h("s6a_probe_manifest_",probe_manifest)
        result[PROTOCOL_DIR/f"synthetic_probe_manifest_{identity}.json"]=(probe_manifest,identity)
    return result
