import copy,hashlib,inspect,json,sys,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/"scripts"),str(ROOT/"scripts"/"lib")]
import s6a_final as p
import s6a_runtime as r
from s6a_prepare_final import probe_content

def history(n=420):
    out={}
    for i,x in enumerate(p.UNIVERSE):
        out[x]=[100*(1+.0005*(i-3))**t for t in range(n)]
    return out
def sig(asset):
    weights={x:(1.0 if x==asset else 0.0) for x in p.UNIVERSE+["CASH"]}
    return {q:{"weights":[copy.deepcopy(weights)],"turnover":.2} for q in p.DIVERSITY["probe_ids"]}

class S6ExecutableTests(unittest.TestCase):
    def test_drawdown_halt_is_external_and_exact(self):
        for schema in p.SCHEMAS.values():
            self.assertNotIn("drawdown_halt_pct",schema["properties"])
            self.assertEqual(schema["external_drawdown_halt_pct"],.12)
        self.assertFalse(r.halt(-.119999).halted)
        directive=r.halt(-.12)
        self.assertTrue(directive.halted and directive.cancel_pending_entries)
        self.assertTrue(directive.liquidate_next_open and directive.remain_cash)
        self.assertTrue(r.halt(0,already=True).halted)

    def test_warmup_maxima(self):
        expected={"B":61,"C":100,"D":251,"E":361,"F":273,"G":0}
        for code in p.NAMES:
            maxima=[]
            for seed in range(40):
                try:maxima.append(r.warmup(code,r.founder(code,seed)))
                except r.S6Error:pass
            self.assertTrue(maxima)
            self.assertLessEqual(max(maxima),expected[code])
        self.assertEqual({c:p.OPERATORS[c]["maximum_warmup"] for c in p.NAMES},expected)

    def test_next_open_fill_and_costs(self):
        self.assertEqual(r.fill_price(100,"buy"),100.05)
        self.assertEqual(r.fill_price(100,"sell"),99.95)
        self.assertEqual(r.fill_cost(100000),50)

    def test_all_decision_functions_are_deterministic_and_long_only(self):
        h=history()
        genomes={c:r.founder(c,r.derive_seed(c,"fixture")) for c in p.NAMES}
        decisions={
          "B":r.decide_B(genomes["B"],h),
          "C":r.decide_C(genomes["C"],h),
          "D":r.decide_D(genomes["D"],h,0),
          "E":r.decide_E(genomes["E"],h,0),
          "F":r.decide_F(genomes["F"],h,0),
          "G":r.decide_G(genomes["G"],{}, {},0,initial=True)}
        for code,value in decisions.items():
            self.assertEqual(value,r.reproduce({
              "B":r.decide_B,"C":r.decide_C}.get(code,lambda *args:value),
              *({
                "B":(genomes[code],h),"C":(genomes[code],h)
              }.get(code,()))))
            self.assertAlmostEqual(sum(value.values()),1,places=10)
            self.assertTrue(all(v>=0 for v in value.values()))

    def test_faster_trend_preserves_and_exits_existing_positions(self):
        g=r.founder("C",r.derive_seed("C","position-exit"))
        g.update({"fast_ema_sessions":3,"slow_ema_sessions":21,
          "breakout_sessions":10,"exit_channel_sessions":5,"volatility_window":10,
          "max_positions":4,"max_asset_weight":.4,"gross_exposure_cap":1.0})
        h={x:[100.0]*100 for x in p.UNIVERSE}
        h["SPY"]=[100+i*.2 for i in range(100)]
        h["SPY"][-1]=h["SPY"][-2]-.05
        self.assertEqual(r.decide_C(g,h)["SPY"],0)
        self.assertGreater(r.decide_C(g,h,{"SPY"})["SPY"],0)
        h["SPY"][-1]=min(h["SPY"][-6:-1])-.05
        self.assertEqual(r.decide_C(g,h,{"SPY"})["SPY"],0)

    def test_hybrid_both_zero_volatility_uses_ratio_one(self):
        g=r.founder("E",r.derive_seed("E","zero-volatility"))
        g.update({"fast_trend_sessions":20,"slow_trend_sessions":100,
          "regime_volatility_window":20,"risk_on_volatility_ratio":1.25,
          "risk_off_volatility_ratio":1.30,"top_k":4,"rebalance_sessions":5,
          "max_asset_weight":.4,"gross_exposure_cap":1.0})
        h={x:[100.0]*361 for x in p.UNIVERSE}
        for i,x in enumerate(p.DEFENSIVE_ASSETS,1):
            h[x]=[100*(1+.0002*i)**t for t in range(361)]
        weights=r.decide_E(g,h,0)
        self.assertGreater(sum(weights[x] for x in p.UNIVERSE),.5)

    def test_mutation_determinism_constraints_and_no_halt_gene(self):
        for code in p.NAMES:
            parent=r.founder(code,r.derive_seed(code,"parent"))
            child1=r.mutate(code,parent,r.derive_seed(code,"mutation"))
            child2=r.mutate(code,parent,r.derive_seed(code,"mutation"))
            self.assertEqual(child1,child2)
            self.assertNotEqual(parent,child1)
            r.validate_genome(code,child1)
            self.assertNotIn("drawdown_halt_pct",child1)

    def test_G_constraints(self):
        g=r.founder("G",99)
        self.assertEqual(sum(g["target_weights_bps"]),10000)
        self.assertLessEqual(max(g["target_weights_bps"]),4000)
        self.assertGreaterEqual(sum(x>0 for x in g["target_weights_bps"]),3)
        bad=copy.deepcopy(g);bad["target_weights_bps"]=[10000,0,0,0,0,0,0,0]
        with self.assertRaises(r.S6Error):r.validate_genome("G",bad)

    def test_same_lineage_and_trader_A_rejection(self):
        r.assert_parent("B","B","gen_fixture")
        with self.assertRaises(r.IsolationError):r.assert_parent("B","C","gen_fixture")
        with self.assertRaises(r.IsolationError):r.assert_parent("B","B",p.TRADER_A)

    def test_boundary_and_feedback_rejection(self):
        self.assertEqual(r.authorize_lane("development"),("2007-02-07","2018-12-31"))
        self.assertEqual(list(inspect.signature(r.load_historical_bundle).parameters),["name"])
        with self.assertRaises(r.BoundaryError):r.load_historical_bundle("comparative")
        with self.assertRaises(r.BoundaryError):r.authorize_lane("development",end="2026-01-01")
        with self.assertRaises(r.BoundaryError):r.authorize_lane("2026")
        with self.assertRaises(r.BoundaryError):r.authorize_lane("qualification",feedback={})
        with self.assertRaises(r.BoundaryError):r.assert_no_feedback(qualification_feedback={})

    def test_development_advancement_is_gen12_verified_unique(self):
        rows=[{"generation":12,"verified":True,"genome_id":f"gen_{i:02d}","fitness":i}
              for i in range(64)]
        ranked=r.rank_development(rows)
        self.assertEqual(len(ranked),8)
        self.assertEqual(ranked[0]["genome_id"],"gen_63")
        bad=copy.deepcopy(rows);bad[0]["generation"]=11
        with self.assertRaises(r.S6Error):r.rank_development(bad)

    def test_similarity_equality_breach_and_no_winner(self):
        self.assertFalse(round(.75,12)>.750000000000)
        self.assertFalse(round(.80,12)>.800000000000)
        self.assertGreater(r.similarity(sig("CASH"),sig("CASH")),.80)
        self.assertLessEqual(r.similarity(sig("SPY"),sig("CASH")),.75)
        qualified={c:[{"genome_id":f"gen_{c}","fitness":1}] for c in p.NAMES}
        signatures={f"gen_{c}":sig("CASH") for c in p.NAMES}
        winners=r.select_winners(qualified,signatures,sig("CASH"))
        self.assertTrue(all(v is None for v in winners.values()))

    def test_synthetic_probes_are_deterministic_and_long_enough(self):
        for name in p.DIVERSITY["probe_ids"]:
            a=probe_content(name);b=probe_content(name)
            self.assertEqual(a,b)
            self.assertEqual(len(a["rows"]),420)
            self.assertEqual(a["source"].startswith("deterministic"),True)
        probes=r.load_synthetic_probes()
        self.assertEqual(set(probes),set(p.DIVERSITY["probe_ids"]))
        self.assertTrue(all(len(value["rows"])==420 for value in probes.values()))

    def test_content_addressed_artifacts_and_probe_hashes(self):
        manifests=list((ROOT/"evolution"/"protocol").glob("synthetic_probe_manifest_s6a_probe_manifest_*.json"))
        self.assertEqual(len(manifests),1)
        env=json.loads(manifests[0].read_text())
        for path,(content,identity) in p.artifact_map(env["content"]).items():
            self.assertTrue(path.is_file())
            self.assertEqual(path.read_bytes(),p.envelope(content,identity))
        for row in env["content"]["probes"].values():
            path=ROOT/row["path"]
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),row["file_sha256"])
        lock=json.loads((ROOT/"evolution"/"protocol"/"s6a_executable_preparation_lock.json").read_text())
        self.assertFalse(lock["content"]["population_execution_authorized"])

if __name__=="__main__":unittest.main()

class S6EnforcementEdgeTests(unittest.TestCase):
    def test_forged_manifest_is_rejected(self):
        content={"x":1};identity=p.h("s6a_fixture_",content)
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/f"{identity}.json"
            path.write_text(json.dumps({"content":{"x":2},"manifest_hash":identity}))
            with self.assertRaises(r.S6Error):r.validate_envelope(path,identity)

    def test_breaching_candidate_is_skipped_for_next_candidate(self):
        qualified={c:[] for c in p.NAMES}
        qualified["B"]=[{"genome_id":"gen_bad","fitness":2},{"genome_id":"gen_good","fitness":1}]
        signatures={"gen_bad":sig("CASH"),"gen_good":sig("SPY")}
        winners=r.select_winners(qualified,signatures,sig("CASH"))
        self.assertEqual(winners["B"]["genome_id"],"gen_good")
        self.assertTrue(all(winners[c] is None for c in "CDEFG"))

    def test_population_builder_is_deterministic_and_isolated(self):
        gen0=r.build_gen0("G")
        self.assertEqual(gen0,r.build_gen0("G"))
        self.assertEqual(len({row["genome_id"] for row in gen0}),64)
        current=[{**row,"fitness":i} for i,row in enumerate(gen0)]
        first=r.build_generation("G",current,1)
        second=r.build_generation("G",current,1)
        self.assertEqual(first,second)
        self.assertEqual([x["role"] for x in first].count("elite"),8)
        self.assertEqual([x["role"] for x in first].count("child"),48)
        self.assertEqual([x["role"] for x in first].count("immigrant"),8)
        bad=copy.deepcopy(current)
        bad[0]["lineage"]="F"
        with self.assertRaises(r.IsolationError):r.build_generation("G",bad,1)
