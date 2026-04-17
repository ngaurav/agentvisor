import os
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture()
def tmp_agentvisor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Redirect all agentvisor I/O to a temp directory and use a fixed key."""
    agentvisor_dir = tmp_path / ".agentvisor"
    agentvisor_dir.mkdir(mode=0o700)

    fixed_key = os.urandom(32)

    monkeypatch.setattr("agentvisor.vault.AGENTVISOR_DIR", agentvisor_dir)
    monkeypatch.setattr("agentvisor.vault.VAULT_DB", agentvisor_dir / "vault.db")
    monkeypatch.setattr("agentvisor.vault.VAULT_KEY_FILE", agentvisor_dir / "vault.key")
    monkeypatch.setattr("agentvisor.vault._load_key", lambda: fixed_key)

    monkeypatch.setattr("agentvisor.registry.AGENTVISOR_DIR", agentvisor_dir)
    monkeypatch.setattr("agentvisor.registry.REGISTRY_FILE", agentvisor_dir / "registry.json")

    return agentvisor_dir
