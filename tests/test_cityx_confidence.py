from scripts.lab_cityx_confidence import wilson_lower


def test_wilson_lower_rewards_more_evidence_at_same_rate():
    assert wilson_lower(40, 100) > wilson_lower(4, 10)
    assert 0 < wilson_lower(40, 100) < .4
