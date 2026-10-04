from gym import generator as G


def test_reference_case_applies_the_frozen_five_percent_participation_cap():
    assert G.CFG["volume_participation_cap"] == 0.05
