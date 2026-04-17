import json
import os
from pathlib import Path

AGENTVISOR_DIR = Path.home() / ".agentvisor"
REGISTRY_FILE = AGENTVISOR_DIR / "registry.json"

AUTH_TYPES = {"bearer", "pat", "oauth-refresh"}


def _ensure_dir() -> None:
    AGENTVISOR_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)


def _load() -> dict:
    if not REGISTRY_FILE.exists():
        return {}
    return json.loads(REGISTRY_FILE.read_text())


def _save(data: dict) -> None:
    _ensure_dir()
    tmp = REGISTRY_FILE.parent / f".registry.tmp.{os.getpid()}"
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(REGISTRY_FILE)


def add(name: str, url: str, auth_type: str, overwrite: bool = False) -> None:
    if auth_type not in AUTH_TYPES:
        raise ValueError(f"Unknown auth type '{auth_type}'. Valid: {sorted(AUTH_TYPES)}")
    data = _load()
    if name in data and not overwrite:
        raise ValueError(f"MCP '{name}' already registered. Use --overwrite to update.")
    data[name] = {"url": url.rstrip("/"), "auth_type": auth_type}
    _save(data)


def get(name: str) -> dict:
    data = _load()
    if name not in data:
        raise KeyError(f"MCP '{name}' not registered. Run: agentvisor mcp-add {name}")
    return data[name]


def list_entries() -> list[dict]:
    data = _load()
    return [{"name": k, **v} for k, v in sorted(data.items())]


def remove(name: str) -> None:
    data = _load()
    if name not in data:
        raise KeyError(f"MCP '{name}' not registered.")
    del data[name]
    _save(data)
