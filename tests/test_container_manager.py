"""Tests for browser/container_manager.py (docker is mocked)."""
import subprocess
from unittest import mock

from browser.container_manager import BrowserContainerManager


def _ok(*a, **k):
    return subprocess.CompletedProcess(a, 0, stdout="abc123\n", stderr="")


def test_ports_do_not_collide_with_docker_compose():
    m = BrowserContainerManager()
    assert m.cdp_port != 9222 and m.novnc_port != 6080
    assert m.container_name != "llm-web-agent-browser"      # the compose container


def test_start_builds_a_safe_docker_run_command():
    m = BrowserContainerManager()
    with mock.patch("browser.container_manager.subprocess.run", side_effect=_ok) as run, \
         mock.patch.object(m, "_wait_for_cdp", return_value=True):
        status = m.start()
    assert status.running and status.cdp_port == m.cdp_port
    cmd = next(c.args[0] for c in run.call_args_list if c.args[0][:2] == ["docker", "run"])
    assert "--init" in cmd and "--rm" in cmd
    assert "127.0.0.1:9223:9222" in cmd                      # CDP, localhost only
    assert "127.0.0.1:6081:6080" in cmd                      # noVNC, localhost only
    assert "--memory=2g" in cmd and "--cpus=2.0" in cmd


def test_start_without_docker_returns_a_clear_error():
    m = BrowserContainerManager()
    with mock.patch("browser.container_manager.subprocess.run", side_effect=FileNotFoundError):
        status = m.start()
    assert not status.running
    assert "docker" in status.error


def test_build_without_docker_does_not_crash():
    m = BrowserContainerManager()
    with mock.patch.object(m, "_image_exists", return_value=False), \
         mock.patch("browser.container_manager.subprocess.run", side_effect=FileNotFoundError):
        m.dockerfile = type(m.dockerfile)(__file__)         # any existing file
        assert m.build_image() is False


def test_start_cleans_up_when_cdp_never_comes_up():
    m = BrowserContainerManager()
    with mock.patch("browser.container_manager.subprocess.run", side_effect=_ok) as run, \
         mock.patch.object(m, "_wait_for_cdp", return_value=False):
        status = m.start()
    assert not status.running and "CDP not available" in status.error
    stops = [c.args[0] for c in run.call_args_list if c.args[0][:2] == ["docker", "stop"]]
    assert len(stops) == 2                                   # before start + cleanup


def test_status_distinguishes_missing_and_unreachable():
    m = BrowserContainerManager()
    with mock.patch.object(m, "_get_container_id", return_value=None):
        assert not m.status().running
    with mock.patch.object(m, "_get_container_id", return_value="abc"), \
         mock.patch.object(m, "_cdp_alive", return_value=False):
        s = m.status()
        assert s.running and s.cdp_port is None and s.error
