"""Tests for dashboard/helpers.py (no FastAPI needed)."""
from types import SimpleNamespace

from dashboard.helpers import (
    missing_variables,
    normalize_domain,
    placeholders_in,
    task_variables,
    valid_domain,
)


def _wf(*templates):
    steps = [SimpleNamespace(value_template=t) for t in templates]
    return SimpleNamespace(steps=steps)


def test_valid_domain_accepts_hostnames_and_ports():
    for d in ("saucedemo.com", "www.saucedemo.com", "localhost:8080", "a-b.example.org"):
        assert valid_domain(d), d


def test_valid_domain_rejects_anything_else():
    for d in ("", "evil.com/path", "user@site.com", "a b.com", "-x.com", "x.com.", "http://x.com",
              "x" * 300):
        assert not valid_domain(d), d


def test_normalize_domain():
    assert normalize_domain("WWW.SauceDemo.com") == "saucedemo.com"
    assert normalize_domain("saucedemo.com") == "saucedemo.com"


def test_placeholders_and_missing_variables():
    wf = _wf("{username}", None, "{password}", "plain text", "{first_name} {last_name}")
    assert placeholders_in(wf) == {"username", "password", "first_name", "last_name"}
    assert missing_variables(wf, {"username": "u", "password": "p"}) == ["first_name", "last_name"]
    assert missing_variables(wf, {"username": "u", "password": "p",
                                  "first_name": "a", "last_name": "b"}) == []


def test_workflow_without_templates_needs_nothing():
    assert missing_variables(_wf(None, None), {}) == []


def test_task_variables_for_the_saucedemo_demo():
    # www.saucedemo.com in the task and saucedemo.com in the database must match.
    v = task_variables()[("saucedemo.com", "checkout")]
    assert v["username"] == "standard_user"
    assert "password" in v
