import pytest

from agentvisor import vault


def test_store_and_get(tmp_agentvisor):
    vault.store("github", "ghp_secret123")
    assert vault.get("github") == "ghp_secret123"


def test_store_duplicate_raises(tmp_agentvisor):
    vault.store("github", "ghp_first")
    with pytest.raises(ValueError, match="already exists"):
        vault.store("github", "ghp_second")


def test_store_overwrite(tmp_agentvisor):
    vault.store("github", "ghp_first")
    vault.store("github", "ghp_second", overwrite=True)
    assert vault.get("github") == "ghp_second"


def test_get_missing_raises(tmp_agentvisor):
    with pytest.raises(KeyError, match="github"):
        vault.get("github")


def test_list_empty(tmp_agentvisor):
    assert vault.list_credentials() == []


def test_list_multiple(tmp_agentvisor):
    vault.store("context7", "key_c7")
    vault.store("github", "ghp_secret")
    entries = vault.list_credentials()
    # alphabetical order
    assert [e["service"] for e in entries] == ["context7", "github"]


def test_revoke(tmp_agentvisor):
    vault.store("github", "ghp_secret")
    vault.revoke("github")
    with pytest.raises(KeyError):
        vault.get("github")


def test_revoke_missing_raises(tmp_agentvisor):
    with pytest.raises(KeyError, match="github"):
        vault.revoke("github")


def test_encryption_roundtrip(tmp_agentvisor):
    secret = "super-secret-token-with-unicode-☺"
    vault.store("test", secret)
    assert vault.get("test") == secret
