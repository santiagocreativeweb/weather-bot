from scripts.export_data import SOURCES


def test_lamp_forward_and_scores_are_exported():
    files = {row[0] for row in SOURCES}
    assert "lamp_shadow_forward.csv" in files
    assert "lamp_shadow_results.csv" in files
    assert "lamp_shadow_verdict.csv" in files
