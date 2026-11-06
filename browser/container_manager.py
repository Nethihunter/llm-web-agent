"""Isolated browser container manager (used by ``--browser container``).

Runs a single Chrome instance (non-headless: Xvfb + noVNC) in Docker to protect
the host machine from resource exhaustion. This is a deliberate isolation
choice, not a multi-session or multi-account tool.

Why Docker?
- Chrome can use several GB of RAM on heavy sites (SPAs, ad-laden pages)
- On an 8th-gen i5 with 20GB total, a runaway Chrome can slow the host
- Docker limits (--memory=2g --cpus=2) keep the host responsive
- Reproducibility: same Chrome version across machines

What this does NOT do:
- No VPN, no IP masking
- No multi-account, no profile rotation
- No auto-restart on failure (single run per invocation)

Ports: the host-side ports differ from docker-compose's ``browser`` service
(9222 / 6080) so that both can run at the same time without a conflict.
"""
from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

import requests

DEFAULT_CDP_PORT = 9223      # host side; docker-compose uses 9222
DEFAULT_NOVNC_PORT = 6081    # host side; docker-compose uses 6080
DEFAULT_IMAGE = "llm-web-agent-browser:latest"
DEFAULT_CONTAINER_NAME = "llm-web-agent-browser-local"


@dataclass
class ContainerStatus:
    running: bool
    cdp_port: int | None
    container_id: str | None
    error: str | None = None


class BrowserContainerManager:
    """Manages a single Chrome container for isolated automation."""

    def __init__(
        self,
        image: str = DEFAULT_IMAGE,
        container_name: str = DEFAULT_CONTAINER_NAME,
        cdp_port: int = DEFAULT_CDP_PORT,
        novnc_port: int = DEFAULT_NOVNC_PORT,
        memory_limit: str = "2g",
        cpus: float = 2.0,
        dockerfile: Path | None = None,
    ):
        self.image = image
        self.container_name = container_name
        self.cdp_port = cdp_port
        self.novnc_port = novnc_port
        self.memory_limit = memory_limit
        self.cpus = cpus
        self.dockerfile = dockerfile or (Path(__file__).parent / "Dockerfile.browser")

    # ------------------------------------------------------------------
    def build_image(self, force: bool = False) -> bool:
        """Build the browser image if it doesn't exist."""
        if not force and self._image_exists():
            print(f"✅ Image '{self.image}' already built.")
            return True

        if not self.dockerfile.exists():
            print(f"❌ Dockerfile not found: {self.dockerfile}")
            return False

        print(f"🔨 Building image '{self.image}'...")
        try:
            subprocess.run(
                [
                    "docker", "build",
                    "-f", str(self.dockerfile),
                    "-t", self.image,
                    str(self.dockerfile.parent),
                ],
                check=True, capture_output=True, text=True, timeout=600,
            )
            return True
        except FileNotFoundError:
            print("❌ 'docker' command not found. Install Docker or use --browser local.")
            return False
        except subprocess.CalledProcessError as e:
            print(f"❌ Build failed:\n{(e.stderr or '')[-500:]}")
            return False
        except subprocess.TimeoutExpired:
            print("❌ Build timeout (>10 min)")
            return False

    def start(self, wait_timeout: float = 20.0) -> ContainerStatus:
        """Start the container and wait for CDP to be ready."""
        self.stop()

        print(f"🚀 Starting container '{self.container_name}'...")
        try:
            subprocess.run(
                [
                    "docker", "run",
                    "-d", "--rm", "--init",
                    "--name", self.container_name,
                    "--shm-size=2g",
                    f"--memory={self.memory_limit}",
                    f"--cpus={self.cpus}",
                    "-p", f"127.0.0.1:{self.cdp_port}:9222",     # localhost only
                    "-p", f"127.0.0.1:{self.novnc_port}:6080",   # live view
                    self.image,
                ],
                check=True, capture_output=True, text=True, timeout=30,
            )
        except FileNotFoundError:
            return ContainerStatus(False, None, None,
                                   error="'docker' command not found")
        except subprocess.TimeoutExpired:
            return ContainerStatus(False, None, None, error="docker run: timeout")
        except subprocess.CalledProcessError as e:
            return ContainerStatus(False, None, None,
                                   error=f"docker run: {(e.stderr or '')[:300]}")

        if not self._wait_for_cdp(wait_timeout):
            logs = self._get_logs()
            self.stop()
            return ContainerStatus(
                False, None, None,
                error=f"CDP not available after {wait_timeout}s.\n"
                      f"Last logs:\n{logs[-500:]}",
            )

        container_id = self._get_container_id()
        print(f"✅ Container ready. CDP: {self.cdp_url()} | live view: {self.novnc_url()}")
        return ContainerStatus(True, self.cdp_port, container_id)

    def stop(self) -> bool:
        """Stop and remove the container (idempotent)."""
        try:
            subprocess.run(
                ["docker", "stop", self.container_name],
                check=False, capture_output=True, timeout=15,
            )
            return True
        except Exception:
            return False

    def status(self) -> ContainerStatus:
        """Check if the container is running and CDP is reachable."""
        container_id = self._get_container_id()
        if not container_id:
            return ContainerStatus(False, None, None)
        if self._cdp_alive():
            return ContainerStatus(True, self.cdp_port, container_id)
        return ContainerStatus(True, None, container_id,
                               error="Container running but CDP unreachable")

    def cdp_url(self) -> str:
        return f"http://127.0.0.1:{self.cdp_port}"

    def novnc_url(self) -> str:
        return f"http://127.0.0.1:{self.novnc_port}/vnc.html?autoconnect=true&resize=scale"

    # ------------------------------------------------------------------
    def _image_exists(self) -> bool:
        try:
            result = subprocess.run(
                ["docker", "images", "-q", self.image],
                capture_output=True, text=True, timeout=5,
            )
            return bool(result.stdout.strip())
        except Exception:
            return False

    def _get_container_id(self) -> str | None:
        try:
            result = subprocess.run(
                ["docker", "ps", "-q", "-f", f"name=^{self.container_name}$"],
                capture_output=True, text=True, timeout=5,
            )
            return result.stdout.strip() or None
        except Exception:
            return None

    def _cdp_alive(self) -> bool:
        try:
            r = requests.get(f"http://127.0.0.1:{self.cdp_port}/json/version", timeout=2)
            return r.status_code == 200
        except Exception:
            return False

    def _wait_for_cdp(self, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._cdp_alive():
                return True
            time.sleep(0.5)
        return False

    def _get_logs(self, tail: int = 50) -> str:
        try:
            result = subprocess.run(
                ["docker", "logs", "--tail", str(tail), self.container_name],
                capture_output=True, text=True, timeout=5,
            )
            return result.stdout + result.stderr
        except Exception:
            return ""
