#!/usr/bin/env python3
"""Materialize immutable S6 protocols and synthetic probes; no market execution."""
import hashlib,json,math,subprocess
from pathlib import Path
import s6a_final as p

def factor(probe,t):
    if probe=="shock_then_reversal":
        return -.015 if 100<=t<140 else (.012 if 140<=t<200 else .0002)
    if probe=="persistent_uptrend": return .001
    if probe=="broad_risk_off": return -.0012 if t>=80 else .0004
    if probe=="regime_transition": return .0003 if t<140 else (.0025*math.sin(t/2) if t<280 else -.0005)
    if probe=="cross_sectional_dispersion": return .0007
    return .00005*math.sin(t/15)

def probe_content(probe):
    prices={x:100+3*i for i,x in enumerate(p.UNIVERSE)};rows=[]
    for t in range(p.DIVERSITY["sessions_per_probe"]):
        assets={}
        common=factor(probe,t)
        for i,x in enumerate(p.UNIVERSE):
            if probe=="cross_sectional_dispersion": r=common*(i-3.5)
            elif probe=="broad_risk_off" and x in p.DEFENSIVE_ASSETS:r=-common*.5
            elif probe=="shock_then_reversal":r=common*(1+.08*i)
            elif probe=="quiet_allocation_drift":r=common+.00002*(i-3.5)
            else:r=common*(1+.04*i)+.0001*math.sin((t+i*7)/11)
            previous=prices[x];raw_open=previous*(1+.00015*math.sin((t+1)*(i+1)))
            prices[x]=max(1,previous*(1+r))
            assets[x]={"raw_open":round(raw_open,8),"adjusted_close":round(prices[x],8)}
        rows.append({"synthetic_session":t,"assets":assets})
    return {"schema_version":1,"probe_id":probe,"sessions":420,
      "source":"deterministic mathematical construction; no historical observations",
      "generator":"s6a_prepare_final.py::probe_content","rows":rows}

def write_immutable(path,payload):
    path.parent.mkdir(parents=True,exist_ok=True)
    if path.exists():
        if path.read_bytes()!=payload:raise SystemExit(f"changed immutable artifact: {path}")
        return
    with path.open("xb") as f:f.write(payload)

def main():
    head=subprocess.check_output(["git","rev-parse","HEAD"],cwd=p.ROOT,text=True).strip()
    if head!=p.BASELINE:raise SystemExit("accepted baseline HEAD changed")
    probe_manifest={"schema_version":1,"sessions_per_probe":420,"historical_rows":0,"probes":{}}
    for probe in p.DIVERSITY["probe_ids"]:
        content=probe_content(probe);identity=p.h(f"s6a_probe_{probe}_",content)
        path=p.PROBE_DIR/f"{identity}.json";payload=p.envelope(content,identity)
        write_immutable(path,payload)
        probe_manifest["probes"][probe]={"identity":identity,
          "path":str(path.relative_to(p.ROOT)),"file_sha256":hashlib.sha256(payload).hexdigest()}
    artifacts=p.artifact_map(probe_manifest)
    for path,(content,identity) in artifacts.items():write_immutable(path,p.envelope(content,identity))
    probe_manifest_hash=p.h("s6a_probe_manifest_",probe_manifest)
    lock={"schema_version":2,"phase":"S6A_EXECUTABLE_PREPARATION",
      "baseline":p.BASELINE,"schema_hashes":p.SCHEMA_HASHES,"run_ids":p.RUN_IDS,
      "protocol_hashes":p.HASHES,"plan_hash":p.PLAN_HASH,
      "probe_manifest_hash":probe_manifest_hash,
      "population_execution_authorized":False,"real_populations":0,
      "persisted_real_genomes":0,"historical_organism_executions":0,
      "historical_qualification_executions":0,"real_mutations":0,
      "trader_A_executions":0,"broker_connections":0,"paper_orders":0,
      "real_orders":0,"live_feeds":0,"alpaca_access":0,"kestrel_access":0}
    lock_id=p.h("s6a_completion_lock_",lock)
    write_immutable(p.PROTOCOL_DIR/"s6a_executable_preparation_lock.json",p.envelope(lock,lock_id))
    print(json.dumps({"plan_hash":p.PLAN_HASH,"lock_hash":lock_id,
      "schema_hashes":p.SCHEMA_HASHES,"run_ids":p.RUN_IDS,
      "protocol_hashes":p.HASHES,"probe_manifest_hash":probe_manifest_hash,
      "population_execution_authorized":False},sort_keys=True))

if __name__=="__main__":main()
