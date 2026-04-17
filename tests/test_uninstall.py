"""Tests for `agentvisor uninstall [--keep-vault]`."""
import json
import shutil
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

import agentvisor.config as config_mod
import agentvisor.service as svc
from agentvisor.cli import cli


@pytest.fixture()
def tmp_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Redirect all I/O to tmp_path for both config and service modules."""
    agentvisor_dir = tmp_path / ".agentvisor"
    log_dir = agentvisor_dir / "logs"
    agentvisor_dir.mkdir(mode=0o700)
    log_dir.mkdir()

    claude_json = tmp_path / ".claude.json"
    managed_keys = agentvisor_dir / "claude_managed_keys.json"
    plist_path = tmp_path / "LaunchAgents" / f"{svc.LABEL}.plist"
    pid_file = agentvisor_dir / "proxy.pid"

    monkeypatch.setattr(config_mod, "CLAUDE_JSON", claude_json)
    monkeypatch.setattr(config_mod, "AGENTVISOR_DIR", agentvisor_dir)
    monkeypatch.setattr(config_mod, "MANAGED_KEYS_FILE", managed_keys)
    monkeypatch.setattr(svc, "AGENTVISOR_DIR", agentvisor_dir)
    monkeypatch.setattr(svc, "LOG_DIR", log_dir)
    monkeypatch.setattr(svc, "PID_FILE", pid_file)
    monkeypatch.setattr(svc, "PLIST_PATH", plist_path)

    # Stub out subprocess calls inside service.uninstall / stop_background
    with patch("shutil.which", return_value="/usr/local/bin/agentvisor"):
        yield {
            "agentvisor_dir": agentvisor_dir,
            "claude_json": claude_json,
            "plist_path": plist_path,
            "pid_file": pid_file,
        }


ENTRIES = [
    {"name": "github", "url": "https://api.githubcopilot.com/mcp/", "auth_type": "pat"},
    {"name": "context7", "url": "https://mcp.context7.com/mcp", "auth_type": "bearer"},
]


def _setup_managed_config(tmp_env):
    """Write managed MCP entries into the fake ~/.claude.json."""
    config_mod.upsert(ENTRIES, port=9090)


def _install_fake_plist(tmp_env):
    """Write a dummy plist so is_installed() returns True."""
    tmp_env["plist_path"].parent.mkdir(parents=True, exist_ok=True)
    tmp_env["plist_path"].write_bytes(b"<plist/>")


def test_uninstall_removes_claude_config(tmp_env):
    _setup_managed_config(tmp_env)
    assert tmp_env["claude_json"].exists()

    with patch.object(svc, "uninstall"), patch.object(svc, "stop_background", side_effect=RuntimeError):
        result = CliRunner().invoke(cli, ["uninstall", "--yes"])

    assert result.exit_code == 0, result.output
    data = json.loads(tmp_env["claude_json"].read_text())
    assert "mcpServers" not in data


def test_uninstall_deletes_agentvisor_dir_by_default(tmp_env):
    _setup_managed_config(tmp_env)
    agentvisor_dir = tmp_env["agentvisor_dir"]
    assert agentvisor_dir.exists()

    with patch.object(svc, "uninstall"), patch.object(svc, "stop_background", side_effect=RuntimeError):
        result = CliRunner().invoke(cli, ["uninstall", "--yes"])

    assert result.exit_code == 0, result.output
    assert not agentvisor_dir.exists()


def test_uninstall_keep_vault_preserves_agentvisor_dir(tmp_env):
    _setup_managed_config(tmp_env)
    agentvisor_dir = tmp_env["agentvisor_dir"]

    with patch.object(svc, "uninstall"), patch.object(svc, "stop_background", side_effect=RuntimeError):
        result = CliRunner().invoke(cli, ["uninstall", "--yes", "--keep-vault"])

    assert result.exit_code == 0, result.output
    assert agentvisor_dir.exists()


def test_uninstall_calls_service_uninstall(tmp_env):
    with patch.object(svc, "stop_background", side_effect=RuntimeError), \
         patch.object(svc, "uninstall") as mock_uninstall:
        CliRunner().invoke(cli, ["uninstall", "--yes"])

    mock_uninstall.assert_called_once()


def test_uninstall_stops_background_proxy(tmp_env):
    with patch.object(svc, "stop_background") as mock_stop, \
         patch.object(svc, "uninstall"):
        CliRunner().invoke(cli, ["uninstall", "--yes"])

    mock_stop.assert_called_once()


def test_uninstall_tolerates_proxy_not_running(tmp_env):
    """stop_background RuntimeError must not abort the uninstall."""
    with patch.object(svc, "stop_background", side_effect=RuntimeError("no proxy")), \
         patch.object(svc, "uninstall"):
        result = CliRunner().invoke(cli, ["uninstall", "--yes"])

    assert result.exit_code == 0


def test_uninstall_no_managed_entries_still_succeeds(tmp_env):
    with patch.object(svc, "stop_background", side_effect=RuntimeError), \
         patch.object(svc, "uninstall"):
        result = CliRunner().invoke(cli, ["uninstall", "--yes"])

    assert result.exit_code == 0
    assert "No managed config entries" in result.output


def test_uninstall_requires_confirmation_without_yes(tmp_env):
    """Without --yes, should prompt. Abort on 'n'."""
    with patch.object(svc, "stop_background", side_effect=RuntimeError), \
         patch.object(svc, "uninstall"):
        result = CliRunner().invoke(cli, ["uninstall"], input="n\n")

    assert result.exit_code != 0
