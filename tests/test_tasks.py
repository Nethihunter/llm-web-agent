"""Unit tests for the demo tasks."""
import importlib


VALID_TASK_NAMES = ["saucedemo_checkout", "booking_search", "wikipedia_extract"]


def test_all_tasks_load():
    for name in VALID_TASK_NAMES:
        mod = importlib.import_module(f"tasks.{name}")
        assert hasattr(mod, "TASK")
        task = mod.TASK
        assert "url" in task
        assert "goal" in task
        assert "name" in task
        assert task["url"].startswith("https://")


def test_saucedemo_has_credentials():
    from tasks.saucedemo_checkout import TASK
    assert "username" in TASK["variables"]
    assert "password" in TASK["variables"]


def test_booking_has_dates():
    from tasks.booking_search import TASK
    assert "checkin" in TASK["variables"]
    assert "checkout" in TASK["variables"]
