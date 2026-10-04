import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent.parent
REPO = HERE.parents[1]
for p in (str(HERE), str(REPO / "scripts"), str(REPO / "scripts" / "lib"), str(REPO / "experiments" / "sam_dev_staging_20261001"),
          str(REPO / "experiments" / "sam_dev_staging_20261001" / "alpaca_adapter"), str(REPO / "experiments" / "sam_dev_staging_20261001" / "kim_order_simulation_tests")):
    if p not in sys.path:
        sys.path.insert(0, p)


@pytest.fixture(scope="session")
def cal():
    from gym import calibration as C
    return C.build(REPO / "data" / "normalized")


@pytest.fixture(scope="session")
def small_world(cal):
    from gym import generator as G
    return G.generate_world(G.WorldSpec("TEST-GYM", "engineering", "recession_credit_shock", 0), cal)
