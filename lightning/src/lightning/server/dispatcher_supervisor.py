from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

from lightning.server.models import ModelProvider

LOG = logging.getLogger("lightning.dispatcher")

_PROVIDER_ENV_KEYS = ("PI_MODEL", "PI_BASE_URL", "PI_API_KEY", "PI_PROVIDER_API")


def find_dispatch_config() -> Path | None:
    """Locate dispatch.yaml for the auto-started dispatcher.

    Search order:
      1. LIGHTNING_DISPATCH_CONFIG env var.
      2. Walk up from the package source tree for a dispatch.yaml.
    """
    override = os.environ.get("LIGHTNING_DISPATCH_CONFIG")
    if override:
        path = Path(override).expanduser()
        return path if path.is_file() else None
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "dispatch.yaml"
        if candidate.is_file():
            return candidate
        if parent == Path(parent.anchor):
            break
    return None


def _providers_fingerprint(providers: list[ModelProvider]) -> str:
    enabled = sorted(
        (p.name, p.api, p.base_url, p.model, p.api_key, p.context_window) for p in providers if p.enabled
    )
    return repr(enabled)


class DispatcherSupervisor:
    """Owns the dispatcher subprocess lifecycle.

    - sync() starts the dispatcher when at least one model provider is enabled,
      restarts it when the providers change, and stops it when none remain.
    - A daemon monitor restarts a crashed dispatcher while providers stay enabled.
    """

    def __init__(
        self,
        dispatch_config: Path | None,
        *,
        command_factory=None,
        restart_backoff: float = 5.0,
    ):
        self._dispatch_config = dispatch_config
        self._command_factory = command_factory or self._default_command
        self._restart_backoff = restart_backoff
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self._fingerprint: str | None = None
        self._should_run = False
        self._closed = False
        self._monitor = threading.Thread(target=self._monitor_loop, name="dispatcher-supervisor", daemon=True)
        self._monitor.start()

    @staticmethod
    def _default_command(config: Path) -> list[str]:
        return [sys.executable, "-m", "lightning", "dispatch", "--config", str(config)]

    @property
    def running(self) -> bool:
        proc = self._proc
        return proc is not None and proc.poll() is None

    def sync(self, providers: list[ModelProvider]) -> None:
        fingerprint = _providers_fingerprint(providers)
        enabled_count = sum(1 for provider in providers if provider.enabled)
        with self._lock:
            if enabled_count == 0:
                self._should_run = False
                self._stop_locked()
                return
            if self._dispatch_config is None:
                LOG.warning("dispatch.yaml not found; dispatcher auto-start disabled")
                self._should_run = False
                return
            self._should_run = True
            if self.running and fingerprint == self._fingerprint:
                return
            self._stop_locked()
            self._start_locked(fingerprint)

    def shutdown(self) -> None:
        self._closed = True
        with self._lock:
            self._should_run = False
            self._stop_locked()

    def _start_locked(self, fingerprint: str) -> None:
        assert self._dispatch_config is not None
        cmd = self._command_factory(self._dispatch_config)
        LOG.info("auto-starting dispatcher: %s", " ".join(cmd))
        try:
            proc = subprocess.Popen(
                cmd,
                cwd=str(self._dispatch_config.parent),
                env=dict(os.environ),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError as exc:
            LOG.error("failed to start dispatcher: %s", exc)
            self._proc = None
            return
        self._proc = proc
        self._fingerprint = fingerprint

    def _stop_locked(self) -> None:
        proc = self._proc
        self._proc = None
        if proc is None or proc.poll() is not None:
            return
        LOG.info("stopping dispatcher pid=%s", proc.pid)
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass

    def _monitor_loop(self) -> None:
        while not self._closed:
            time.sleep(1)
            restart = False
            with self._lock:
                if (
                    self._should_run
                    and self._dispatch_config is not None
                    and (self._proc is None or self._proc.poll() is not None)
                ):
                    restart = True
            if not restart:
                continue
            LOG.warning("dispatcher is not running; restarting in %.0fs", self._restart_backoff)
            time.sleep(self._restart_backoff)
            with self._lock:
                if self._closed or not self._should_run or self._dispatch_config is None:
                    continue
                if self._proc is None or self._proc.poll() is not None:
                    self._start_locked(self._fingerprint or "")


_supervisor: DispatcherSupervisor | None = None
_supervisor_lock = threading.Lock()


def get_supervisor() -> DispatcherSupervisor | None:
    """Return the shared supervisor, or None when auto-start is disabled."""
    global _supervisor
    enabled = os.environ.get("LIGHTNING_DISPATCH_AUTOSTART", "1").strip().lower()
    if enabled not in ("1", "true", "yes", "on"):
        return None
    with _supervisor_lock:
        if _supervisor is None:
            _supervisor = DispatcherSupervisor(find_dispatch_config())
        return _supervisor


def shutdown_supervisor() -> None:
    global _supervisor
    with _supervisor_lock:
        sup = _supervisor
        _supervisor = None
    if sup is not None:
        sup.shutdown()
