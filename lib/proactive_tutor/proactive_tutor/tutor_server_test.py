import sys

import chz
import memory_mcp.server as memory_mcp_server
from proactive_tutor import packaged_entrypoint, tutor_server


def test_memory_mcp_entrypoint_mode_bypasses_tutor_cli(monkeypatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(sys, "argv", ["tutor-server", "--memory-mcp"])
    monkeypatch.setattr(memory_mcp_server, "main", lambda: calls.append("memory"))
    monkeypatch.setattr(
        chz,
        "entrypoint",
        lambda *args, **kwargs: calls.append("tutor"),
    )

    packaged_entrypoint.main()

    assert calls == ["memory"]


def test_normal_entrypoint_mode_uses_tutor_cli(monkeypatch) -> None:
    calls: list[tuple[object, bool]] = []
    monkeypatch.setattr(sys, "argv", ["tutor-server", "model_name=test-model"])
    monkeypatch.setattr(
        chz,
        "entrypoint",
        lambda entrypoint, *, allow_hyphens: calls.append((entrypoint, allow_hyphens)),
    )

    packaged_entrypoint.main()

    assert calls == [(tutor_server.main, True)]
