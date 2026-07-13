from types import SimpleNamespace

from scripts.lab_station_mos import base_values


def test_station_mos_candidate_family_is_deterministic():
    row = SimpleNamespace(GFS=80, NAM=82, MEX=84, NBS=86, NBE=88, LAV=90,
                          mu_cityx=85)
    got = base_values(row)
    assert got["MOSMED"] == 84
    assert got["NBM2"] == 87
    assert got["STACK6"] == 85
    assert got["MOSCITY50"] == 84.5
    assert got["NBMCITY50"] == 86
    assert got["STACKCITY50"] == 85
