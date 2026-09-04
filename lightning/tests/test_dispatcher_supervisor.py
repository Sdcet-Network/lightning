from __future__ import annotations

import sys

from lightning.server.dispatcher_supervisor import DispatcherSupervisor, find_dispatch_config, get_supervisor
from lightning.server.models import ModelProvider


def _provider(name: str = "p", *, enabled: bool = True) -> ModelProvider:
    return ModelProvider(
        name=name,
        api="openai-completions",
        base_url="http://api",
        model="m",
        api_key="k",
        enabled=enabled,
    )


def _sleep_command(_config) -> list[str]:
    return [sys.executable, "-c", "import time; time.sleep(60)"]


def test_supervisor_starts_stays_and_stops_with_providers(tmp_path) -> None:
    supervisor = DispatcherSupervisor(tmp_path / "dispatch.yaml", command_factory=_sleep_command)
    try:
        assert not supervisor.running

        supervisor.sync([_provider()])
        assert supervisor.running

        supervisor.sync([_provider()])
        assert supervisor.running

        supervisor.sync([])
        assert not supervisor.running
    finally:
        supervisor.shutdown()


def test_supervisor_restarts_when_provider_changes(tmp_path) -> None:
    supervisor = DispatcherSupervisor(tmp_path / "dispatch.yaml", command_factory=_sleep_command)
    try:
        supervisor.sync([_provider("a")])
        pid_a = supervisor._proc.pid

        supervisor.sync([_provider("b")])
        assert supervisor.running
        assert supervisor._proc.pid != pid_a
    finally:
        supervisor.shutdown()


def test_supervisor_ignores_disabled_providers(tmp_path) -> None:
    supervisor = DispatcherSupervisor(tmp_path / "dispatch.yaml", command_factory=_sleep_command)
    try:
        supervisor.sync([_provider(enabled=False)])
        assert not supervisor.running
    finally:
        supervisor.shutdown()


def test_get_supervisor_disabled_by_env(monkeypatch) -> None:
    monkeypatch.setenv("LIGHTNING_DISPATCH_AUTOSTART", "0")
    assert get_supervisor() is None


def test_find_dispatch_config_respects_env(monkeypatch, tmp_path) -> None:
    config = tmp_path / "dispatch.yaml"
    config.write_text("server: http://x\n", encoding="utf-8")
    monkeypatch.setenv("LIGHTNING_DISPATCH_CONFIG", str(config))

    assert find_dispatch_config() == config
