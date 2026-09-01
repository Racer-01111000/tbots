"""Real-path S6A development-feasibility contract tests.

These tests generate candidates only in memory. They never persist a population,
run an episode, compute fitness, or import the interrupted S6B evaluator.
"""
import copy
import sys
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/"scripts"),str(ROOT/"scripts"/"lib")]
import s6a_final as p
import s6a_runtime as r


class S6ADevelopmentFeasibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bundle=r.load_historical_bundle("development")
        cls.availability=r.development_history_availability(cls.bundle)

    def genome(self,code,**changes):
        g=r.founder(code,r.derive_seed(code,"feasibility-fixture",sorted(changes.items())))
        g.update(changes)
        r.validate_genome(code,g)
        return g

    def infeasible_e(self):
        return self.genome("E",fast_trend_sessions=90,slow_trend_sessions=300,
          regime_volatility_window=90)

    def feasible_e(self):
        return self.genome("E",fast_trend_sessions=20,slow_trend_sessions=100,
          regime_volatility_window=20)

    def infeasible_f(self):
        return self.genome("F",relative_strength_lookbacks=[200,252],
          skip_recent_sessions=20)

    def feasible_f(self):
        return self.genome("F",relative_strength_lookbacks=[20,200],
          skip_recent_sessions=20)

    def test_exact_physical_availability_is_immutable_development_only(self):
        summary=r.development_feasibility_summary(self.availability)
        self.assertEqual(summary["episode_count"],12)
        self.assertEqual(summary["minimum_pre_episode_price_bars"],252)
        self.assertEqual(summary["minimum_price_bars_including_episode_start"],253)
        self.assertEqual(summary["minimum_price_bars_by_asset"]["DBC"],253)
        self.assertEqual(
          {value for key,value in summary["minimum_price_bars_by_asset"].items()
            if key!="DBC"},{379})
        self.assertEqual(summary["binding_points"],[{"episode_index":0,
          "first_session":"2007-02-07","asset":"DBC","available_price_bars":253}])
        self.assertEqual(summary["source_bundle_latest"],"2018-12-31")
        self.assertLess(summary["latest_observation_used_for_feasibility"],"2019-01-01")
        self.assertTrue(all(date<"2019-01-01" for date in summary["episode_first_sessions"]))

    def test_b_c_d_g_generated_genomes_are_development_feasible(self):
        for code in "BCDG":
            for seed in range(16):
                g=r.founder(code,r.derive_seed(code,"feasible-sample",seed))
                admission=r.require_development_feasible(
                  code,g,availability=self.availability)
                self.assertEqual(admission.genome_id,r.validate_genome(code,g))

    def test_infeasible_e_is_rejected_and_feasible_e_is_accepted(self):
        bad=self.infeasible_e()
        self.assertEqual(r.warmup("E",bad),361)
        with self.assertRaises(r.FeasibilityError):
            r.require_development_feasible("E",bad,availability=self.availability)
        good=self.feasible_e()
        self.assertEqual(r.warmup("E",good),101)
        admission=r.require_development_feasible(
          "E",good,availability=self.availability)
        self.assertEqual(admission.required_price_bars,101)

    def test_infeasible_f_is_rejected_and_feasible_f_is_accepted(self):
        bad=self.infeasible_f()
        self.assertEqual(r.warmup("F",bad),273)
        with self.assertRaises(r.FeasibilityError):
            r.require_development_feasible("F",bad,availability=self.availability)
        good=self.feasible_f()
        self.assertEqual(r.warmup("F",good),221)
        admission=r.require_development_feasible(
          "F",good,availability=self.availability)
        self.assertEqual(admission.required_price_bars,221)

    def test_rejection_precedes_persistence_fitness_and_episode_execution(self):
        calls={"persistence":0,"fitness":0,"episodes":0}
        def forbidden_action(admission):
            calls["persistence"]+=1
            calls["fitness"]+=1
            calls["episodes"]+=12
            return admission
        with self.assertRaises(r.FeasibilityError):
            r.with_development_feasible_candidate(
              "E",self.infeasible_e(),forbidden_action,
              availability=self.availability)
        self.assertEqual(calls,{"persistence":0,"fitness":0,"episodes":0})
        audit=r.new_feasibility_audit()
        self.assertFalse(r._candidate_is_feasible(
          "F",self.infeasible_f(),self.availability,audit,"founder"))
        self.assertEqual(audit["rejected_total"],1)
        self.assertIsNone(audit["fitness_effect"])
        self.assertEqual(audit["episodes_executed"],0)
        self.assertEqual(audit["population_slots_consumed"],0)

    def test_deterministic_feasibility_resampling_and_generation(self):
        first_audit=r.new_feasibility_audit()
        second_audit=r.new_feasibility_audit()
        first=r.build_gen0("E",availability=self.availability,audit=first_audit)
        second=r.build_gen0("E",availability=self.availability,audit=second_audit)
        self.assertEqual(first,second)
        self.assertEqual(first_audit,second_audit)
        self.assertEqual(len(first),64)
        self.assertGreater(first_audit["rejected_total"],0)
        current=[{**row,"fitness":index} for index,row in enumerate(first)]
        generation_audit=r.new_feasibility_audit()
        generation=r.build_generation("E",current,1,
          availability=self.availability,audit=generation_audit)
        self.assertEqual(len(generation),64)
        self.assertEqual([row["role"] for row in generation].count("child"),48)
        self.assertEqual([row["role"] for row in generation].count("immigrant"),8)
        for row in generation:
            r.require_development_feasible(
              "E",row["genome"],availability=self.availability)

    def test_every_lineage_can_fill_required_in_memory_cardinality(self):
        for code in p.NAMES:
            rows=r.build_gen0(code,availability=self.availability)
            self.assertEqual(len(rows),64)
            self.assertEqual(len({row["genome_id"] for row in rows}),64)
            for row in rows:
                r.require_development_feasible(
                  code,row["genome"],availability=self.availability)

    def test_2026_and_caller_selected_ranges_remain_inaccessible(self):
        with self.assertRaises(r.BoundaryError):r.authorize_lane("2026")
        with self.assertRaises(r.BoundaryError):
            r.authorize_lane("development",end="2026-01-01")
        bad=copy.deepcopy(self.availability)
        bad["episodes"][0]["available_price_bars"]["DBC"]=10
        with self.assertRaises(r.FeasibilityError):
            r.require_development_feasible(
              "B",r.founder("B",1),availability=bad)


if __name__=="__main__":
    unittest.main()
