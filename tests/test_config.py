import json
from pathlib import Path

import pytest

import agentvisor.config as config_mod


@pytest.fixture()
def tmp_claude(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    claude_json = tmp_path / ".claude.json"
    managed_keys = tmp_path / ".agentvisor" / "claude_managed_keys.json"
    (tmp_path / ".agentvisor").mkdir(mode=0o700)

    monkeypatch.setattr(config_mod, "CLAUDE_JSON", claude_json)
    monkeypatch.setattr(config_mod, "AGENTVISOR_DIR", tmp_path / ".agentvisor")
    monkeypatch.setattr(config_mod, "MANAGED_KEYS_FILE", managed_keys)
    return claude_json


ENTRIES = [
    {"name": "github", "url": "https://api.githubcopilot.com/mcp", "auth_type": "pat"},
    {"name": "context7", "url": "https://mcp.context7.com/mcp", "auth_type": "bearer"},
]


def test_upsert_writes_proxy_urls(tmp_claude):
    config_mod.upsert(ENTRIES, port=9090)
    data = json.loads(tmp_claude.read_text())
    assert data["mcpServers"]["github"]["url"] == "http://127.0.0.1:9090/github"
    assert data["mcpServers"]["context7"]["url"] == "http://127.0.0.1:9090/context7"
    assert data["mcpServers"]["github"]["type"] == "http"


def test_upsert_is_idempotent(tmp_claude):
    config_mod.upsert(ENTRIES, port=9090)
    config_mod.upsert(ENTRIES, port=9090)
    data = json.loads(tmp_claude.read_text())
    assert len(data["mcpServers"]) == 2


def test_upsert_preserves_existing_keys(tmp_claude):
    tmp_claude.write_text(json.dumps({"projects": {"foo": "bar"}, "otherKey": 1}))
    config_mod.upsert(ENTRIES, port=9090)
    data = json.loads(tmp_claude.read_text())
    assert data["projects"] == {"foo": "bar"}
    assert data["otherKey"] == 1
    assert "mcpServers" in data


def test_upsert_tracks_managed_keys(tmp_claude):
    config_mod.upsert(ENTRIES, port=9090)
    managed = json.loads(
        (tmp_claude.parent / ".agentvisor" / "claude_managed_keys.json").read_text()
    )
    assert "github" in managed
    assert "context7" in managed


def test_remove_removes_named_entries(tmp_claude):
    config_mod.upsert(ENTRIES, port=9090)
    config_mod.remove(["github"])
    data = json.loads(tmp_claude.read_text())
    assert "github" not in data["mcpServers"]
    assert "context7" in data["mcpServers"]


def test_remove_all_managed(tmp_claude):
    config_mod.upsert(ENTRIES, port=9090)
    removed = config_mod.remove_all_managed()
    assert sorted(removed) == ["context7", "github"]
    data = json.loads(tmp_claude.read_text())
    assert "mcpServers" not in data


def test_remove_all_managed_empty(tmp_claude):
    removed = config_mod.remove_all_managed()
    assert removed == []


def test_render_does_not_write(tmp_claude):
    snippet = config_mod.render(ENTRIES, port=9090)
    assert not tmp_claude.exists()
    data = json.loads(snippet)
    assert data["mcpServers"]["github"]["url"] == "http://127.0.0.1:9090/github"


def test_rollback_on_service_failure(tmp_claude):
    original = {"projects": {"existing": True}}
    tmp_claude.write_text(json.dumps(original))

    def failing_install():
        raise RuntimeError("launchctl exploded")

    with pytest.raises(RuntimeError, match="launchctl exploded"):
        config_mod.upsert_with_service(ENTRIES, port=9090, install_fn=failing_install)

    # ~/.claude.json must be restored to original state
    restored = json.loads(tmp_claude.read_text())
    assert restored == original
    assert "mcpServers" not in restored


def test_rollback_creates_file_when_none_existed(tmp_claude):
    """If ~/.claude.json didn't exist before, rollback deletes it."""
    assert not tmp_claude.exists()

    def failing_install():
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        config_mod.upsert_with_service(ENTRIES, port=9090, install_fn=failing_install)

    assert not tmp_claude.exists()


def test_upsert_port_override(tmp_claude):
    config_mod.upsert(ENTRIES, port=8888)
    data = json.loads(tmp_claude.read_text())
    assert ":8888/" in data["mcpServers"]["github"]["url"]
