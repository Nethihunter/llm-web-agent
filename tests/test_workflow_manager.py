"""Unit tests for workflow_manager.py (always on a throw-away SQLite file)."""
import tempfile
from pathlib import Path

from workflow_manager import Step, Workflow, WorkflowManager


def _manager():
    # backend="sqlite" is explicit: inside the web container DB_BACKEND=postgres
    # is set, and these tests must never touch the real database.
    tmp = Path(tempfile.mkdtemp()) / "test.db"
    return WorkflowManager(db_path=tmp, backend="sqlite")


def _set_last_success(m, wf_id, sql_value):
    """Force steps.last_success_at (SQLite only)."""
    conn = m.db.connect()
    try:
        conn.execute(f"UPDATE steps SET last_success_at = {sql_value} WHERE workflow_id = ?",
                     (wf_id,))
        conn.commit()
    finally:
        conn.close()


def test_save_and_load_workflow():
    m = _manager()
    wf = Workflow(
        name="checkout", goal="Buy a product",
        steps=[
            Step(1, "fill", "#email", "{email}"),
            Step(2, "click", "#submit"),
        ],
        success_indicator="h1:has-text('Done')",
    )
    wf_id = m.save_workflow("https://example.com", wf)
    assert wf_id > 0

    loaded = m.load_workflow("https://example.com", "checkout")
    assert loaded is not None
    assert loaded.name == "checkout"
    assert len(loaded.steps) == 2
    assert loaded.steps[0].selector == "#email"
    assert loaded.steps[0].value_template == "{email}"


def test_load_missing_workflow():
    m = _manager()
    assert m.load_workflow("https://example.com", "nope") is None


def test_step_is_fresh_right_after_save():
    # save_workflow stamps last_success_at: the steps were just discovered.
    m = _manager()
    wf_id = m.save_workflow("https://x.com", Workflow(
        name="test", goal="x", steps=[Step(1, "click", "#a")]))
    assert not m.is_step_stale(wf_id, 1)


def test_step_stale_after_seven_days():
    m = _manager()
    wf_id = m.save_workflow("https://x.com", Workflow(
        name="test", goal="x", steps=[Step(1, "click", "#a")]))
    _set_last_success(m, wf_id, "datetime('now', '-8 days')")
    assert m.is_step_stale(wf_id, 1)
    m.mark_step_success(wf_id, 1)
    assert not m.is_step_stale(wf_id, 1)


def test_step_stale_when_it_never_succeeded():
    m = _manager()
    wf_id = m.save_workflow("https://x.com", Workflow(
        name="test", goal="x", steps=[Step(1, "click", "#a")]))
    _set_last_success(m, wf_id, "NULL")
    assert m.is_step_stale(wf_id, 1)


def test_unknown_step_is_stale():
    m = _manager()
    assert m.is_step_stale(999, 1)


def test_step_failure_count():
    m = _manager()
    wf_id = m.save_workflow("https://x.com", Workflow(
        name="t", goal="x", steps=[Step(1, "click", "#a")]))
    m.mark_step_success(wf_id, 1)
    for _ in range(2):
        m.mark_step_failure(wf_id, 1)
    assert not m.is_step_stale(wf_id, 1)        # 2 failures: still usable
    m.mark_step_failure(wf_id, 1)
    assert m.is_step_stale(wf_id, 1)            # 3 failures: stale


def test_run_logging():
    m = _manager()
    run_id = m.log_run_start("https://x.com", None, "test", "discovery")
    m.log_run_end(run_id, "success", steps=5, llm_calls=3,
                  tokens_in=1000, tokens_out=200)
    runs = m.list_runs(limit=10)
    assert len(runs) == 1
    assert runs[0]["status"] == "success"
    assert runs[0]["tokens_in"] == 1000


def test_stats_keep_tokens_in_and_out_separate():
    # Regression: with Postgres both SUM columns used to collapse into one key.
    # (This SQLite test cannot reproduce the Postgres bug, it guards the query.)
    m = _manager()
    r1 = m.log_run_start("https://x.com", None, "a", "discovery")
    m.log_run_end(r1, "success", steps=1, llm_calls=1, tokens_in=1_000_000, tokens_out=0)
    assert m.get_stats()["total_cost"] == "0.1400"      # 1M in * $0.14 / 1M
    r2 = m.log_run_start("https://x.com", None, "b", "discovery")
    m.log_run_end(r2, "success", steps=1, llm_calls=1, tokens_in=0, tokens_out=1_000_000)
    assert m.get_stats()["total_cost"] == "0.4200"      # + 1M out * $0.28 / 1M


def test_delete_workflow():
    m = _manager()
    wf = Workflow(name="t", goal="x", steps=[Step(1, "click", "#a")])
    m.save_workflow("https://x.com", wf)
    assert m.delete_workflow("https://x.com", "t")
    assert m.load_workflow("https://x.com", "t") is None
