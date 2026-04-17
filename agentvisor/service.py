import os
import plistlib
import shutil
import signal
import subprocess
import sys
from pathlib import Path

LABEL = "com.agentvisor.proxy"
AGENTVISOR_DIR = Path.home() / ".agentvisor"
LOG_DIR = AGENTVISOR_DIR / "logs"
PID_FILE = AGENTVISOR_DIR / "proxy.pid"

if sys.platform == "darwin":
    PLIST_PATH = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
else:
    PLIST_PATH = Path.home() / ".config" / "systemd" / "user" / f"{LABEL}.service"


def _agentvisor_bin() -> str:
    path = shutil.which("agentvisor")
    if not path:
        raise RuntimeError("Cannot locate agentvisor binary. Is it in PATH?")
    return path


def _env_snapshot() -> dict[str, str]:
    keys = ["HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy",
            "SSL_CERT_FILE", "REQUESTS_CA_BUNDLE"]
    return {k: os.environ[k] for k in keys if k in os.environ}


def _ensure_log_dir() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)


# ── plist / unit generation ──────────────────────────────────────────────────

def generate_plist(host: str = "127.0.0.1", port: int = 9090) -> bytes:
    """Return launchd plist bytes (macOS). All paths are absolute (no ~/)."""
    _ensure_log_dir()
    log = str(LOG_DIR / "proxy.log")
    plist: dict = {
        "Label": LABEL,
        "ProgramArguments": [
            _agentvisor_bin(), "proxy-start",
            "--host", host, "--port", str(port),
        ],
        "RunAtLoad": True,
        "KeepAlive": True,
        "StandardOutPath": log,
        "StandardErrorPath": log,
    }
    env = _env_snapshot()
    if env:
        plist["EnvironmentVariables"] = env
    return plistlib.dumps(plist)


def generate_systemd_unit(host: str = "127.0.0.1", port: int = 9090) -> str:
    """Return systemd user unit content (Linux). All paths are absolute."""
    _ensure_log_dir()
    log = LOG_DIR / "proxy.log"
    lines = [
        "[Unit]",
        "Description=agentvisor proxy",
        "After=network.target",
        "",
        "[Service]",
        f"ExecStart={_agentvisor_bin()} proxy-start --host {host} --port {port}",
        "Restart=on-failure",
        f"StandardOutput=append:{log}",
        f"StandardError=append:{log}",
    ]
    for k, v in _env_snapshot().items():
        lines.append(f"Environment={k}={v}")
    lines += ["", "[Install]", "WantedBy=default.target", ""]
    return "\n".join(lines)


# ── service install / uninstall ──────────────────────────────────────────────

def install(host: str = "127.0.0.1", port: int = 9090) -> None:
    """Install and load the OS service."""
    if sys.platform == "darwin":
        _install_launchd(host, port)
    else:
        _install_systemd(host, port)


def _install_launchd(host: str, port: int) -> None:
    PLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    if PLIST_PATH.exists():
        subprocess.run(["launchctl", "unload", str(PLIST_PATH)],
                       check=False, capture_output=True)
    PLIST_PATH.write_bytes(generate_plist(host, port))
    result = subprocess.run(
        ["launchctl", "load", str(PLIST_PATH)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"launchctl load failed: {result.stderr.strip()}")


def _install_systemd(host: str, port: int) -> None:
    PLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    PLIST_PATH.write_text(generate_systemd_unit(host, port))
    subprocess.run(["systemctl", "--user", "daemon-reload"],
                   check=True, capture_output=True)
    result = subprocess.run(
        ["systemctl", "--user", "enable", "--now", LABEL],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"systemctl enable failed: {result.stderr.strip()}")


def uninstall() -> None:
    """Stop, unload, and remove the OS service file."""
    if sys.platform == "darwin":
        if PLIST_PATH.exists():
            subprocess.run(["launchctl", "unload", str(PLIST_PATH)],
                           check=False, capture_output=True)
            PLIST_PATH.unlink()
    else:
        if PLIST_PATH.exists():
            subprocess.run(["systemctl", "--user", "disable", "--now", LABEL],
                           check=False, capture_output=True)
            PLIST_PATH.unlink()
            subprocess.run(["systemctl", "--user", "daemon-reload"],
                           check=False, capture_output=True)


def is_installed() -> bool:
    return PLIST_PATH.exists()


def is_running() -> bool:
    """Return True if the proxy OS service is active."""
    if sys.platform == "darwin":
        result = subprocess.run(
            ["launchctl", "print", f"gui/{os.getuid()}/{LABEL}"],
            capture_output=True,
        )
        return result.returncode == 0
    else:
        result = subprocess.run(
            ["systemctl", "--user", "is-active", LABEL],
            capture_output=True,
        )
        return result.stdout.strip() == b"active"


# ── background process (non-service mode) ────────────────────────────────────

def start_background(host: str = "127.0.0.1", port: int = 9090) -> int:
    """Spawn proxy detached from terminal. Writes PID file. Returns PID."""
    _ensure_log_dir()
    AGENTVISOR_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    log_fh = open(str(LOG_DIR / "proxy.log"), "a")
    proc = subprocess.Popen(
        [_agentvisor_bin(), "proxy-start", "--host", host, "--port", str(port)],
        start_new_session=True,
        stdout=log_fh,
        stderr=subprocess.STDOUT,
        close_fds=True,
    )
    PID_FILE.write_text(str(proc.pid))
    return proc.pid


def background_pid() -> int | None:
    """Return PID of running background proxy, or None if not running."""
    if not PID_FILE.exists():
        return None
    try:
        pid = int(PID_FILE.read_text().strip())
        os.kill(pid, 0)  # raises ProcessLookupError if gone
        return pid
    except (ValueError, ProcessLookupError, PermissionError):
        PID_FILE.unlink(missing_ok=True)
        return None


def stop_background() -> None:
    """Terminate the background proxy process."""
    pid = background_pid()
    if pid is None:
        raise RuntimeError("No running background proxy found.")
    os.kill(pid, signal.SIGTERM)
    PID_FILE.unlink(missing_ok=True)
