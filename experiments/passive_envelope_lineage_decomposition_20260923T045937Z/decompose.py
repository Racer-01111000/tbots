import json
import statistics as st

ROOT = "/home/rick/tbots/experiments"
with open(f"{ROOT}/controls_comparison_20260921T064013Z/results.json") as f:
    real = json.load(f)
with open(f"{ROOT}/passive_envelope_dev_gap_20260923T045250Z/passive_envelope_dev_results.json") as f:
    passive = json.load(f)

STRESS_EPISODES = {1, 8}  # 2008-02->2009-02, 2015-02->2016-02
ALL_EPISODES = set(range(12))
NONSTRESS_EPISODES = ALL_EPISODES - STRESS_EPISODES

passive_by_ep = {e["episode_index"]: e for e in passive["passive_envelope_episode_metrics"]}
assert set(passive_by_ep.keys()) == ALL_EPISODES

GROUPS = {
    "S5D_CHAMPION": ["S5D_CHAMPION"],
    "B_primary": [f"B_primary_rank{i}" for i in range(1, 9)],
    "B_reproduction": [f"B_reproduction_rank{i}" for i in range(1, 9)],
    "C_primary": [f"C_primary_rank{i}" for i in range(1, 9)],
    "D_primary": [f"D_primary_rank{i}" for i in range(1, 9)],
    "D_reproduction": [f"D_reproduction_rank{i}" for i in range(1, 9)],
}

# sanity: confirm no overlap / exact partition of 41 real agents (44 total incl 3 synthetic controls)
all_ids = set()
for ids in GROUPS.values():
    all_ids.update(ids)
missing = all_ids - set(real["results"].keys())
assert not missing, missing
real_agent_keys = [k for k, v in real["results"].items() if v["agent_meta"]["kind"] == "evolved" or k == "S5D_CHAMPION"]
print("real evolved+champion agent count in results.json:", len(real_agent_keys))
print("grouped count:", len(all_ids))
print("symmetric diff:", set(real_agent_keys) ^ all_ids)


def metric_list(agent_id, metric, episodes):
    ep_metrics = real["results"][agent_id]["episode_metrics"]
    return [ep_metrics[e][metric] for e in episodes if ep_metrics[e]["episode_index"] == e or True][0:0] or \
        [next(m for m in ep_metrics if m["episode_index"] == e)[metric] for e in episodes]


def group_pairs(group_ids, episodes):
    """List of (agent_id, episode_index, metrics_dict) for every agent x episode pair."""
    out = []
    for aid in group_ids:
        ep_metrics = real["results"][aid]["episode_metrics"]
        by_idx = {m["episode_index"]: m for m in ep_metrics}
        for e in episodes:
            out.append((aid, e, by_idx[e]))
    return out


def summarize_group(name, group_ids):
    all_pairs = group_pairs(group_ids, ALL_EPISODES)
    n_genomes = len(group_ids)
    n_pairs = len(all_pairs)

    def med(field):
        return st.median(p[2][field] for p in all_pairs)

    medians = {
        "return": med("total_return"),
        "max_drawdown": med("max_drawdown"),
        "sharpe": med("sharpe"),
        "sortino": med("sortino"),
        "turnover": med("turnover"),
    }

    def win_fraction(episodes, metric, better):
        pairs = group_pairs(group_ids, episodes)
        wins = 0
        for aid, e, m in pairs:
            pv = passive_by_ep[e][metric]
            ev = m[metric]
            if better(pv, ev):
                wins += 1
        return wins / len(pairs) if pairs else None

    # "wins" defined as passive beats evolved: higher return better, higher sharpe/sortino better,
    # LESS NEGATIVE (i.e. greater) drawdown is better (drawdown stored as negative number)
    def cmp_return(pv, ev):
        return pv > ev

    def cmp_dd(pv, ev):
        return pv > ev  # less negative = better

    def cmp_sharpe(pv, ev):
        return pv > ev

    def cmp_sortino(pv, ev):
        return pv > ev

    result = {
        "n_genomes": n_genomes,
        "n_agent_episode_pairs": n_pairs,
        "median_evolved": medians,
        "passive_win_fraction_all": {
            "return": win_fraction(ALL_EPISODES, "total_return", cmp_return),
            "max_drawdown": win_fraction(ALL_EPISODES, "max_drawdown", cmp_dd),
            "sharpe": win_fraction(ALL_EPISODES, "sharpe", cmp_sharpe),
            "sortino": win_fraction(ALL_EPISODES, "sortino", cmp_sortino),
        },
        "passive_win_fraction_stress_episodes_1_8": {
            "return": win_fraction(STRESS_EPISODES, "total_return", cmp_return),
            "max_drawdown": win_fraction(STRESS_EPISODES, "max_drawdown", cmp_dd),
            "sharpe": win_fraction(STRESS_EPISODES, "sharpe", cmp_sharpe),
            "sortino": win_fraction(STRESS_EPISODES, "sortino", cmp_sortino),
        },
        "passive_win_fraction_nonstress_10_episodes": {
            "return": win_fraction(NONSTRESS_EPISODES, "total_return", cmp_return),
            "max_drawdown": win_fraction(NONSTRESS_EPISODES, "max_drawdown", cmp_dd),
            "sharpe": win_fraction(NONSTRESS_EPISODES, "sharpe", cmp_sharpe),
            "sortino": win_fraction(NONSTRESS_EPISODES, "sortino", cmp_sortino),
        },
    }
    return result


report = {}
for name, ids in GROUPS.items():
    report[name] = summarize_group(name, ids)

# ---- duplication check B primary vs B reproduction ----
def genome_ids(group):
    return [real["results"][a]["agent_meta"]["genome_id"] for a in GROUPS[group]]

def episode_metrics_tuple(agent_id):
    ep = real["results"][agent_id]["episode_metrics"]
    by_idx = {m["episode_index"]: m for m in ep}
    return tuple(
        (by_idx[e]["total_return"], by_idx[e]["max_drawdown"], by_idx[e]["sharpe"], by_idx[e]["sortino"], by_idx[e]["turnover"])
        for e in range(12)
    )

dup_check = {}
for fam, primary_key, repro_key in [("B", "B_primary", "B_reproduction"), ("D", "D_primary", "D_reproduction")]:
    p_ids = genome_ids(primary_key)
    r_ids = genome_ids(repro_key)
    genome_id_pairs_identical = p_ids == r_ids
    metrics_identical = []
    for i in range(8):
        pa = GROUPS[primary_key][i]
        ra = GROUPS[repro_key][i]
        same_genome = real["results"][pa]["agent_meta"]["genome_id"] == real["results"][ra]["agent_meta"]["genome_id"]
        same_metrics = episode_metrics_tuple(pa) == episode_metrics_tuple(ra)
        metrics_identical.append({"rank": i + 1, "same_genome_id": same_genome, "same_all_12ep_metrics": same_metrics})
    dup_check[fam] = {
        "primary_genome_ids": p_ids,
        "reproduction_genome_ids": r_ids,
        "genome_id_lists_identical_by_rank": genome_id_pairs_identical,
        "per_rank": metrics_identical,
        "all_ranks_fully_identical": all(x["same_genome_id"] and x["same_all_12ep_metrics"] for x in metrics_identical),
    }

out = {
    "stress_episodes": sorted(STRESS_EPISODES),
    "nonstress_episodes": sorted(NONSTRESS_EPISODES),
    "lineage_report": report,
    "duplication_check": dup_check,
}

with open("/home/rick/tbots/experiments/passive_envelope_lineage_decomposition_20260923T045937Z/lineage_decomposition_results.json", "w") as f:
    json.dump(out, f, indent=2, default=str)

print(json.dumps(out, indent=2, default=str))
