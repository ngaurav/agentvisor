# agentvisor

![CI](https://github.com/ngaurav/agentvisor/actions/workflows/ci.yml/badge.svg)

**~/.ssh/agent for AI agents.**

Everyone solves auth by controlling the server. Nobody solves auth for the client who doesn't own the server.

agentvisor fills that gap. It runs a local proxy that sits between your AI agent (Claude Code, Cursor, OpenCode) and the Remote MCP servers it talks to. Credentials live in an encrypted local vault. The proxy injects them on every request. Your agent never sees a raw token. Your config files never contain a secret.

```bash
pipx install agentvisor

agentvisor store github                         # paste your PAT once
agentvisor mcp-add github \
  --url https://api.githubcopilot.com/mcp/ \
  --auth pat
agentvisor mcp-config --proxy                   # writes Claude Code config + installs OS service

# Done. GitHub Remote MCP works in Claude Code.
# No token in your config. No manual restart. It just works.
```

---

## What it does

Remote MCP servers require Bearer tokens. Today developers hardcode them in `~/.claude.json` or `.env` files. Tokens expire silently. Configs get stale. Credentials end up in git history.

agentvisor intercepts the calls, injects the right credential from its vault, and handles token refresh automatically. The agent sees an authenticated HTTP call. The developer sees nothing — because there's nothing to manage.

```
Claude Code
    │ http://localhost:9090/github
    ▼
agentvisor proxy (127.0.0.1:9090)
    │ vault.get("github") → Authorization: Bearer <token>
    ▼
https://mcp.github.com   (authenticated)
```

---

## How it works

### Vault

Credentials are stored in `~/.agentvisor/vault.db` — a SQLite database with AES-256-GCM encrypted blobs per row. The encryption key lives in the OS keychain (`keyring` library: macOS Keychain, SecretService on Linux). On headless Linux with no keyring daemon, the key falls back to `~/.agentvisor/vault.key` (0600 permissions) with a one-time warning.

### Proxy

A starlette ASGI server on `127.0.0.1:9090`. Path-based routing: every registered MCP server gets a path prefix. `/github/*` goes to `mcp.github.com`, `/context7/*` to `mcp.context7.com`. On each request, the proxy:

1. Looks up the route in `~/.agentvisor/registry.json`
2. Gets the token from the vault (refreshes lazily if within 60s of expiry, for oauth-refresh type)
3. Injects `Authorization: Bearer <token>` into the outgoing request
4. Streams the response back without buffering (SSE-safe)

A single `httpx.AsyncClient` is shared across all requests for connection pooling. One `asyncio.Lock` per service prevents double-refresh within the proxy process.

### Auto-start

`agentvisor mcp-config --proxy` installs an OS-native login service:

- **macOS:** `~/Library/LaunchAgents/com.agentvisor.proxy.plist` + `launchctl load`
- **Linux:** `~/.config/systemd/user/agentvisor-proxy.service` + `systemctl --user enable`

The proxy starts at login and stays running. launchd/systemd handle restart-on-crash. You never have to think about it.

### Port

Fixed at `9090`. If occupied: `ERR_PORT_IN_USE` — resolve the conflict, then retry. No port fallback, no config drift.

---

## Install

```bash
pipx install agentvisor
```

Requires Python 3.10+. macOS or Linux (Windows is not supported in v1).

---

## CLI reference

### Vault

```bash
agentvisor store <service>           # store a static API key or PAT
                                    # reads from TTY (not echoed); --secret-stdin for scripting
                                    # --overwrite to update an existing entry
agentvisor auth <service>            # OAuth browser flow (stores access + refresh token)
agentvisor get <service>             # print valid token to stdout (lazy refresh for OAuth)
agentvisor list                      # show all stored credentials + expiry status
agentvisor revoke <service>          # delete all credentials for a service
```

### MCP registry

```bash
agentvisor mcp-add <name> \
  --url <remote-mcp-url> \
  --auth bearer|pat|oauth-refresh   # auth type (see below)
  # prerequisite: agentvisor store <name> must exist first
  # --overwrite to update an existing entry
  # stored in ~/.agentvisor/registry.json

agentvisor mcp-list                  # show all registered MCP entries
agentvisor mcp-remove <name>         # deregister (does not revoke vault credential)
```

**Auth types:**

| Type | Behavior |
|------|----------|
| `bearer` | Static token. `Authorization: Bearer <token>`. Never refreshed. |
| `pat` | Alias for bearer. GitHub PAT semantics (no expiry, no refresh). |
| `oauth-refresh` | Short-lived OAuth token. Lazy refresh 60s before expiry. On upstream 401: force-refresh once, retry. If still 401: `503 ERR_TOKEN_REJECTED`. |

### Config generation

```bash
agentvisor mcp-config                # upsert managed mcpServers in ~/.claude.json
                                    # leaves user-added entries untouched
agentvisor mcp-config --proxy        # use localhost:9090/<name> URLs + install OS service
agentvisor mcp-config --unproxy      # remove proxy entries + stop + unload OS service
agentvisor mcp-config --dry-run      # preview what will be written (v1.5)
agentvisor mcp-config --stdout       # print JSON to stdout, write nothing
agentvisor mcp-config --out <path>   # write to specified path
```

Exit codes: `1` = `ERR_REFRESH_FAILED:<service>`, `2` = `ERR_VAULT_LOCKED`

### Proxy lifecycle

```bash
agentvisor proxy-start               # start proxy in foreground (Ctrl-C to stop)
agentvisor proxy-start --background  # ephemeral background start
                                    # no-op if OS service is already running
                                    # no-op if proxy.pid exists and process is live
agentvisor proxy-stop                # SIGTERM to proxy.pid
agentvisor proxy-status              # process, HTTP health, per-route vault check
```

### Uninstall

```bash
agentvisor uninstall                 # stop proxy + remove OS service + remove Claude config
                                    # + delete ~/.agentvisor/ (vault, registry, logs)
agentvisor uninstall --keep-vault    # same but keep vault and registry intact
```

### Proxy HTTP responses

| Code | Error | Meaning |
|------|-------|---------|
| 404 | `ERR_UNKNOWN_SERVICE` | Path prefix not in MCP registry |
| 503 | `ERR_REFRESH_FAILED:<svc>` | OAuth refresh rejected by endpoint |
| 503 | `ERR_TOKEN_REJECTED:<svc>` | Upstream 401 after refresh (or immediately for bearer/pat) |
| 503 | `ERR_NOT_FOUND:<svc>` | No vault entry — run `agentvisor store <svc>` |
| 503 | `ERR_VAULT_LOCKED` | Vault could not be decrypted |
| 503 | `ERR_UPSTREAM_UNREACHABLE` | Upstream MCP server unreachable |
| 504 | `ERR_UPSTREAM_TIMEOUT` | Upstream timed out (default 30s) |
| 429 | (passed through) | Rate limited; adds `X-Agentvisor-Ratelimited: true` header |

Error bodies are JSON: `{"error": "ERR_...", "message": "human-readable explanation"}`.

### Health

```bash
curl http://localhost:9090/health
# {"status": "ok", "uptime_s": 7234}

curl http://localhost:9090/__agentvisor__/status
# {"routes": [{"name": "github", "upstream": "mcp.github.com", "auth": "pat"}], "uptime_s": 7234, "last_crash": null}
```

---

## Architecture

```
~/.agentvisor/
  vault.db          AES-256-GCM SQLite (keyring + cryptography)
  registry.json     MCP route registry (name, url, auth-type, vault key)
  claude_managed_keys.json  tracks which mcpServers keys agentvisor owns
  proxy.pid         PID of background proxy process (if --background)
  proxy.log         crash log for background mode + launchd/systemd stderr

OS Keychain ──► vault_key (32-byte AES key, via keyring library)
Fallback (headless Linux): ~/.agentvisor/vault.key (0600)

┌──────────────────────────────────────────────────────────────┐
│ Developer machine                                             │
│                                                              │
│  agentvisor proxy (127.0.0.1:9090)                            │
│  starlette + httpx AsyncClient (shared, connection-pooled)   │
│                                                              │
│  GET /health              → 200 {"status": "ok"}             │
│  GET /__agentvisor__/status → 200 {routes, uptime, ...}       │
│  /github/*   → vault.get("github")  → Bearer inject          │
│  /context7/* → vault.get("context7") → Bearer inject         │
│                                                              │
│  asyncio.Lock per service (single-process refresh guard)     │
│  SSE: streams chunks via aiter_bytes(), never buffers        │
│                                                              │
│  Auto-start:                                                  │
│  macOS: ~/Library/LaunchAgents/com.agentvisor.proxy.plist     │
│  Linux: ~/.config/systemd/user/agentvisor-proxy.service       │
│                                                              │
└─────────────────────────┬────────────────────────────────────┘
                          │ HTTPS + Bearer token (injected)
                          ▼
              Remote MCP servers
              mcp.github.com, mcp.context7.com, ...

Claude Code config (~/.claude.json, written by mcp-config --proxy):
  {
    "mcpServers": {
      "github":   { "type": "http", "url": "http://localhost:9090/github" },
      "context7": { "type": "http", "url": "http://localhost:9090/context7" }
    }
  }
```

---

## Dependencies

| Library | Purpose |
|---------|---------|
| `keyring` | OS keychain (macOS Keychain, SecretService on Linux) |
| `cryptography` | AES-256-GCM encryption (AESGCM primitive) |
| `sqlite3` | Vault storage (stdlib, no extra dep) |
| `starlette` | ASGI proxy server |
| `httpx` | Async HTTP client with streaming support |
| `asyncclick` | Async CLI framework |

---

## Known limitations

- **Any local process can use the proxy.** The OS user boundary is the security boundary. The proxy binds to `127.0.0.1` only. If you need per-agent authorization scoping, that's v2.
- **Cross-process refresh race.** If `agentvisor get <service>` and the proxy both trigger an OAuth refresh simultaneously, you may get `invalid_grant`. v1 targets are static PATs so this doesn't apply. Fix in v2 via SQLite EXCLUSIVE transaction.
- **`mcp-remove` doesn't clean up OS service or Claude config.** Re-run `agentvisor mcp-config --proxy` after removing a service. Use `mcp-config --unproxy` to remove all proxy config.
- **Corporate proxy / custom CA.** The launchd plist captures `HTTP_PROXY`, `HTTPS_PROXY`, `SSL_CERT_FILE`, and `REQUESTS_CA_BUNDLE` from your environment at install time. If those change, re-run `agentvisor mcp-config --proxy`.

---

## Platform support

| Platform | Status |
|----------|--------|
| macOS (arm64, x86_64) | v1 |
| Linux | v1 |
| Windows | Not supported |

---

## What's not in this tool

- **No cloud signup.** Everything runs locally.
- **No agent identity verification.** Any process on the machine can call the proxy.
- **No OpenAPI support.** Remote MCP only in v1.
- **No team credential sharing.** That's the v2 enterprise model.

---

## Roadmap

### v1 (current)
- [x] Encrypted local vault (AES-256-GCM, OS keychain)
- [x] Local auth-injecting proxy (starlette + httpx, SSE streaming)
- [x] Path-based multi-MCP routing on a single port (9090)
- [x] Auto-start via launchd (macOS) / systemd user service (Linux)
- [x] `agentvisor mcp-config --proxy` — installs OS service + writes Claude Code config
- [x] `agentvisor mcp-config --unproxy` — teardown proxy config + OS service
- [x] `agentvisor uninstall [--keep-vault]` — full teardown

### v1.5
- [ ] `agentvisor mcp-config --dry-run` — preview before committing
- [ ] Multi-client support (Cursor, OpenCode)
- [ ] Claude Desktop support

### v2
- [ ] OpenAPI spec-aware auth injection
- [ ] Enterprise: managed vault, team credentials, audit logs, RBAC

---

## Security model

The vault key is machine-local. If the OS keychain is wiped or you migrate to a new machine, credentials must be re-entered. There is no cross-machine sync in v1.

The proxy binds to `127.0.0.1` only. Any process running as your OS user can call it. The OS user boundary is the security boundary. This is the same model as `ssh-agent`.

Nonces for AES-256-GCM are generated fresh per encryption operation (`os.urandom(12)`). They are prepended to the ciphertext blob. Nonces are never reused.
