import pytest

from agentvisor import registry


def test_add_and_get(tmp_agentvisor):
    registry.add("github", "https://api.githubcopilot.com/mcp/", "pat")
    entry = registry.get("github")
    assert entry["url"] == "https://api.githubcopilot.com/mcp"  # trailing slash stripped
    assert entry["auth_type"] == "pat"


def test_add_duplicate_raises(tmp_agentvisor):
    registry.add("github", "https://api.githubcopilot.com/mcp/", "pat")
    with pytest.raises(ValueError, match="already registered"):
        registry.add("github", "https://api.githubcopilot.com/mcp/", "pat")


def test_add_overwrite(tmp_agentvisor):
    registry.add("github", "https://api.githubcopilot.com/mcp/", "pat")
    registry.add("github", "https://api.githubcopilot.com/mcp/v2/", "bearer", overwrite=True)
    entry = registry.get("github")
    assert entry["auth_type"] == "bearer"
    assert "v2" in entry["url"]


def test_add_invalid_auth_type(tmp_agentvisor):
    with pytest.raises(ValueError, match="Unknown auth type"):
        registry.add("github", "https://example.com", "magic")


def test_get_missing_raises(tmp_agentvisor):
    with pytest.raises(KeyError, match="github"):
        registry.get("github")


def test_list_empty(tmp_agentvisor):
    assert registry.list_entries() == []


def test_list_multiple(tmp_agentvisor):
    registry.add("context7", "https://mcp.context7.com/mcp", "bearer")
    registry.add("github", "https://api.githubcopilot.com/mcp", "pat")
    entries = registry.list_entries()
    assert [e["name"] for e in entries] == ["context7", "github"]


def test_remove(tmp_agentvisor):
    registry.add("github", "https://api.githubcopilot.com/mcp", "pat")
    registry.remove("github")
    with pytest.raises(KeyError):
        registry.get("github")


def test_remove_missing_raises(tmp_agentvisor):
    with pytest.raises(KeyError, match="github"):
        registry.remove("github")


def test_atomic_write_creates_file(tmp_agentvisor):
    registry.add("github", "https://api.githubcopilot.com/mcp", "pat")
    registry_file = tmp_agentvisor / "registry.json"
    assert registry_file.exists()
