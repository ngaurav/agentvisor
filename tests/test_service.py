import os
import plistlib
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

import agentvisor.service as svc


@pytest.fixture()
def tmp_service(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Redirect all service I/O to tmp_path."""
    agentvisor_dir = tmp_path / ".agentvisor"
    log_dir = agentvisor_dir / "logs"
    agentvisor_dir.mkdir(mode=0o700)
    log_dir.mkdir()

    plist_path = tmp_path / "LaunchAgents" / f"{svc.LABEL}.plist"
    pid_file = agentvisor_dir / "proxy.pid"

    monkeypatch.setattr(svc, "AGENTVISOR_DIR", agentvisor_dir)
    monkeypatch.setattr(svc, "LOG_DIR", log_dir)
    monkeypatch.setattr(svc, "PID_FILE", pid_file)
    monkeypatch.setattr(svc, "PLIST_PATH", plist_path)

    with patch("shutil.which", return_value="/usr/local/bin/agentvisor"):
        yield {
            "agentvisor_dir": agentvisor_dir,
            "log_dir": log_dir,
            "plist_path": plist_path,
            "pid_file": pid_file,
        }


# ── plist generation (macOS) ─────────────────────────────────────────────────

@pytest.mark.skipif(sys.platform != "darwin", reason="macOS only")
def test_plist_no_tilde_in_paths(tmp_service):
    plist_bytes = svc.generate_plist()
    plist = plistlib.loads(plist_bytes)
    # No path in the plist should start with ~
    all_values = str(plist)
    assert "~/" not in all_values


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS only")
def test_plist_structure(tmp_service):
    plist_bytes = svc.generate_plist(host="127.0.0.1", port=9090)
    plist = plistlib.loads(plist_bytes)

    assert plist["Label"] == svc.LABEL
    assert plist["RunAtLoad"] is True
    assert plist["KeepAlive"] is True
    assert "--port" in plist["ProgramArguments"]
    assert "9090" in plist["ProgramArguments"]
    assert "--host" in plist["ProgramArguments"]
    assert "proxy-start" in plist["ProgramArguments"]
    assert plist["ProgramArguments"][0] == "/usr/local/bin/agentvisor"


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS only")
def test_plist_captures_env_vars(tmp_service, monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://corp-proxy:3128")
    monkeypatch.setenv("SSL_CERT_FILE", "/etc/ssl/certs/ca-bundle.crt")

    plist_bytes = svc.generate_plist()
    plist = plistlib.loads(plist_bytes)

    assert "EnvironmentVariables" in plist
    assert plist["EnvironmentVariables"]["HTTPS_PROXY"] == "http://corp-proxy:3128"
    assert plist["EnvironmentVariables"]["SSL_CERT_FILE"] == "/etc/ssl/certs/ca-bundle.crt"


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS only")
def test_plist_no_env_section_when_empty(tmp_service, monkeypatch):
    for k in ["HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy",
              "SSL_CERT_FILE", "REQUESTS_CA_BUNDLE"]:
        monkeypatch.delenv(k, raising=False)

    plist_bytes = svc.generate_plist()
    plist = plistlib.loads(plist_bytes)
    assert "EnvironmentVariables" not in plist


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS only")
def test_plist_log_paths_are_absolute(tmp_service):
    plist_bytes = svc.generate_plist()
    plist = plistlib.loads(plist_bytes)
    assert Path(plist["StandardOutPath"]).is_absolute()
    assert Path(plist["StandardErrorPath"]).is_absolute()


# ── systemd unit generation (Linux) ─────────────────────────────────────────

@pytest.mark.skipif(sys.platform == "darwin", reason="Linux only")
def test_systemd_unit_structure(tmp_service):
    unit = svc.generate_systemd_unit()
    assert "[Unit]" in unit
    assert "[Service]" in unit
    assert "[Install]" in unit
    assert "proxy-start" in unit
    assert "/usr/local/bin/agentvisor" in unit


@pytest.mark.skipif(sys.platform == "darwin", reason="Linux only")
def test_systemd_unit_no_tilde(tmp_service):
    unit = svc.generate_systemd_unit()
    assert "~/" not in unit


# ── PID file management ──────────────────────────────────────────────────────

def test_background_pid_returns_none_when_no_pid_file(tmp_service):
    assert svc.background_pid() is None


def test_background_pid_returns_none_for_dead_process(tmp_service):
    tmp_service["pid_file"].write_text("99999999")
    assert svc.background_pid() is None


def test_background_pid_cleans_stale_pid_file(tmp_service):
    tmp_service["pid_file"].write_text("99999999")
    svc.background_pid()
    assert not tmp_service["pid_file"].exists()


def test_background_pid_returns_own_pid(tmp_service):
    own_pid = os.getpid()
    tmp_service["pid_file"].write_text(str(own_pid))
    assert svc.background_pid() == own_pid


def test_is_installed_false_when_no_plist(tmp_service):
    assert svc.is_installed() is False


def test_is_installed_true_when_plist_exists(tmp_service):
    tmp_service["plist_path"].parent.mkdir(parents=True, exist_ok=True)
    tmp_service["plist_path"].write_text("dummy")
    assert svc.is_installed() is True
