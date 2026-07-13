from scripts.lab_metar_precision import parse_body_f, parse_tgroup_f
from scripts.lab_wu_ground_truth import bucket_displacement


def test_metar_precision_groups():
    metar = "KLGA 010051Z 34006KT 10SM BKN100 22/04 A2986 RMK AO2 T02220044 $"
    assert abs(parse_tgroup_f(metar) - 71.96) < 1e-9
    assert abs(parse_body_f(metar) - 71.6) < 1e-9


def test_metar_precision_negative_groups():
    metar = "KORD 011251Z 00000KT 10SM CLR M02/M07 A3012 RMK AO2 T10171067"
    assert abs(parse_tgroup_f(metar) - 28.94) < 1e-9
    assert abs(parse_body_f(metar) - 28.4) < 1e-9


def test_bucket_displacement_has_no_center_bias():
    assert bucket_displacement(73.0, (72, 73)) == 0.0
    assert bucket_displacement(74.0, (72, 73)) == -1.0
    assert bucket_displacement(84.0, (80, None)) == 0.0
