import json
import os
from pathlib import Path
from typing import Callable

CLAUDE_JSON = Path.home() / ".claude.json"
AGENTVISOR_DIR = Path.home() / ".agentvisor"
MANAGED_KEYS_FILE = AGENTVISOR_DIR / "claude_managed_keys.json"

PROXY_HOST = "127.0.0.1"
PROXY_PORT = 9090


def _load_claude() -> dict:
    if not CLAUDE_JSON.exists():
        return {}
    return json.loads(CLAUDE_JSON.read_text())


def _save_claude(data: dict) -> None:
    tmp = CLAUDE_JSON.parent / f".claude.json.tmp.{os.getpid()}"
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(CLAUDE_JSON)


def _load_managed() -> set[str]:
    if not MANAGED_KEYS_FILE.exists():
        return set()
    return set(json.loads(MANAGED_KEYS_FILE.read_text()))


def _save_managed(keys: set[str]) -> None:
    AGENTVISOR_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = MANAGED_KEYS_FILE.parent / f".managed_keys.tmp.{os.getpid()}"
    tmp.write_text(json.dumps(sorted(keys), indent=2))
    tmp.replace(MANAGED_KEYS_FILE)


def proxy_url(name: str, port: int = PROXY_PORT) -> str:
    return f"http://{PROXY_HOST}:{port}/{name}"


def render(entries: list[dict], port: int = PROXY_PORT) -> str:
    """Return the mcpServers JSON snippet without writing anything."""
    mcp_servers = {e["name"]: {"type": "http", "url": proxy_url(e["name"], port)} for e in entries}
    return json.dumps({"mcpServers": mcp_servers}, indent=2)


def upsert(entries: list[dict], port: int = PROXY_PORT) -> None:
    """Write proxy entries into ~/.claude.json. Idempotent."""
    data = _load_claude()
    mcp_servers = data.setdefault("mcpServers", {})
    managed = _load_managed()

    for entry in entries:
        name = entry["name"]
        mcp_servers[name] = {"type": "http", "url": proxy_url(name, port)}
        managed.add(name)

    _save_claude(data)
    _save_managed(managed)


def remove(names: list[str]) -> None:
    """Remove the named entries from ~/.claude.json and the managed-keys index."""
    data = _load_claude()
    mcp_servers = data.get("mcpServers", {})
    managed = _load_managed()

    for name in names:
        mcp_servers.pop(name, None)
        managed.discard(name)

    if mcp_servers:
        data["mcpServers"] = mcp_servers
    else:
        data.pop("mcpServers", None)

    _save_claude(data)
    _save_managed(managed)


def remove_all_managed() -> list[str]:
    """Remove every entry agentvisor owns. Returns sorted list of removed names."""
    managed = _load_managed()
    if managed:
        remove(list(managed))
    return sorted(managed)


def upsert_with_service(
    entries: list[dict],
    port: int,
    install_fn: Callable[[], None],
) -> None:
    """Write config then call install_fn. Rolls back ~/.claude.json on failure."""
    original_bytes = CLAUDE_JSON.read_bytes() if CLAUDE_JSON.exists() else None
    upsert(entries, port)
    try:
        install_fn()
    except Exception:
        if original_bytes is not None:
            CLAUDE_JSON.write_bytes(original_bytes)
        else:
            CLAUDE_JSON.unlink(missing_ok=True)
        raise
