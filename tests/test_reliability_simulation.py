from scripts.simulate_reliability import run_simulation


def test_deterministic_reliability_simulation_has_no_loss_or_duplicates(tmp_path):
    result = run_simulation(
        days=5,
        groups_count=3,
        seed=20260827,
        workdir=tmp_path,
    )

    assert result["ok"] is True
    assert result["runs_found"] == 15
    assert result["sent"] + result["manual_holds"] == 15
    assert result["task_loss"] == 0
    assert result["retry_pending"] == 0
    assert result["duplicate_external_image_calls"] == 0
    assert result["duplicate_successful_image_sends"] == 0
    assert result["duplicate_successful_text_sends"] == 0
    assert result["runtime_reports"] == 5
