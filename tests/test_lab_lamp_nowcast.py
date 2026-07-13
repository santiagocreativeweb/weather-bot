from scripts.lab_lamp_nowcast import corrected_mu


def test_nowcast_correction_is_clipped_and_scaled():
    assert corrected_mu(80, 2, .5) == 81
    assert corrected_mu(80, 20, .5) == 82
    assert corrected_mu(80, -20, 1) == 76
