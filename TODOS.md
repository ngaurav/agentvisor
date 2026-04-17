# TODOS

## v1 — required before public launch

- [ ] **`agentvisor mcp-config --unproxy`** — removes Claude config proxy entries + stops + unloads
  launchd/systemd service. Auth tooling that's hard to uninstall erodes trust.
  Effort: S (human) / XS (CC+gstack). Triggered by: CEO review 2026-04-17.
  **Eng review decision:** implement alongside `mcp-config --proxy` in the same PR (inverse operation, same code surface).

- [ ] **`agentvisor uninstall [--keep-vault]`** — full teardown: OS service, Claude config,
  optionally vault. Security-conscious developers will look for this before adopting.
  Effort: S / XS (CC+gstack). Triggered by: CEO review 2026-04-17.

## v1.5

- [ ] **`agentvisor mcp-config --dry-run`** — preview what mcp-config will write before committing.
  Builds trust with cautious developers. Effort: XS. Triggered by: CEO review 2026-04-17.

- [ ] **Multi-client support** (Cursor, OpenCode) — after Claude Code validation succeeds.

## v2

- [ ] OpenAPI spec-aware auth injection
- [ ] `agentvisor uninstall` auto-cleanup of hook/service (follow-up to v1 uninstall)
- [ ] Enterprise cloud: managed vault, team credentials, audit logs, RBAC

## Nice to have

- [ ] **SSE proxy integration test** — mock MCP server + agentvisor proxy + SSE client,
  assert chunk-by-chunk delivery (no buffering). Safety net for the proxy's most likely
  failure mode. Effort: S. Triggered by: CEO + Codex review 2026-04-17.

- [ ] `agentvisor mcp-config --dry-run` could also preview launchd plist content

## Sprint plan

> Status as of 2026-04-17. Based on CEO review + eng review decisions.
> Two blocking pre-conditions must be resolved before any proxy code is written.

### ✅ Sprint 0 — The Assignment — DONE 2026-04-17

**Goal:** Verify the two architectural pre-conditions manually before writing code.

| Task | Status |
|------|--------|
| Connect Claude Code to GitHub Remote MCP with a hardcoded PAT in `~/.claude.json` | ✅ |
| Connect Claude Code to Context7 MCP with hardcoded API key | ✅ |
| Inspect network tab — confirm SSE transport (not WebSocket) | ✅ SSE confirmed |
| Confirm `~/.claude.json` is the correct config file path | ✅ |
| Document exact auth header format each server expects | ✅ see sprint0-findings.md |
| Document failure mode when token is wrong | ✅ see sprint0-findings.md |

**Gate:** ✅ PASSED — SSE confirmed + `~/.claude.json` path correct → proceeded to Sprint 1.

---

### ✅ Sprint 1 — Vault + basic proxy, manual start — DONE 2026-04-17

**Goal:** End-to-end working proxy with manual `proxy-start --foreground`. No daemon, no launchd.

**Deliverables:**
- ✅ `pyproject.toml` with deps: `keyring`, `cryptography`, `starlette`, `httpx`, `uvicorn`, `click`
- ✅ `agentvisor/vault.py` — AES-256-GCM vault (store, get, list, revoke). Auto-initialize on first run; key in OS keychain or `~/.agentvisor/vault.key` fallback with warning.
- ✅ `agentvisor/registry.py` — read/write `~/.agentvisor/registry.json` with atomic writes
- ✅ `agentvisor/proxy.py` — starlette ASGI: path routing, Bearer injection, SSE streaming (`aiter_bytes()`), shared `AsyncClient`, `asyncio.Lock` per service, all 8 HTTP error codes, JSON error bodies, `/health` + `/__agentvisor__/status`
- ✅ `agentvisor/cli.py` — `store`, `get`, `list`, `revoke`, `mcp-add`, `mcp-list`, `mcp-remove`, `proxy-start --foreground`
- ✅ `tests/` — pytest + respx, 31 tests, all vault + registry + proxy codepaths

**Validation:** ✅ Manual end-to-end confirmed — `agentvisor store` → `agentvisor mcp-add` → `agentvisor proxy-start` → Claude Code with `localhost:9090/github` in config → GitHub MCP tools work.

---

### ✅ Sprint 2 — Config generation + OS service — DONE 2026-04-17

**Goal:** `mcp-config --proxy` installs everything in one command.

**Deliverables:**
- ✅ `agentvisor/config.py` — read/parse `~/.claude.json`, upsert managed entries, write back. Track owned keys in `~/.agentvisor/claude_managed_keys.json`. Rollback `~/.claude.json` if launchd install fails.
- ✅ `agentvisor/service.py` — launchd plist generator: `which agentvisor` + absolute paths (no `~/`), `EnvironmentVariables` key with `HTTP_PROXY`/`HTTPS_PROXY`/`SSL_CERT_FILE`/`REQUESTS_CA_BUNDLE` snapshotted from current env, `StandardOutPath`/`StandardErrorPath` → `proxy.log`. systemd unit generator (Linux). OS service detection (`launchctl print` / `systemctl --user is-active`).
- ✅ CLI: `agentvisor mcp-config [--proxy] [--unproxy] [--stdout] [--out <path>]`
- ✅ CLI: `agentvisor proxy-start --background`, `agentvisor proxy-stop`, `agentvisor proxy-status`
- ✅ Proxy startup: retry vault access 3x (500ms delay) before failing with `ERR_VAULT_LOCKED`
- ✅ Tests: mcp-config idempotency, launchd plist correctness (no `~/`, env vars captured), rollback on launchd failure — 53 tests pass

**Validation:** ✅ Manual end-to-end confirmed — `agentvisor mcp-config --proxy` → Claude Code config has `localhost:9090/github`, launchd service installed, proxy auto-starts on next login.

---

### ✅ Sprint 3 — Hardening + uninstall — DONE 2026-04-17

**Goal:** Complete the trust surface. An auth tool that's hard to uninstall erodes trust.

**Deliverables:**
- ✅ `agentvisor uninstall [--keep-vault]` — stop proxy, unload OS service, remove Claude config entries, optionally delete `~/.agentvisor/`. 8 tests.
- ✅ GitHub Actions CI: `pytest` on push (ubuntu + macOS × Python 3.10/3.11/3.12). PyPI publish on tag push via trusted publishing.
- ✅ README final pass — correct GitHub MCP URL, roadmap checkboxes updated, `uninstall` + improved `proxy-status` documented.
- ✅ `agentvisor proxy-status` — shows process/service state with fix hints, HTTP reachability, per-route vault credential check.

**Validation:** 61 tests pass. Install → use → uninstall. No residue in `~/.claude.json` or `~/Library/LaunchAgents/`.

---

### Sprint 4 — Launch prep (~1 day)

**Goal:** Ship it.

**Deliverables:**
- ✅ Rename package to `agentvisor` (was agentauth → agentgate → agentvisor). 61 tests pass.
- [ ] Create GitHub repo + push initial commit
- [ ] Register `agentvisor` on PyPI + configure trusted publishing (pypi env in GitHub Actions)
- [ ] Tag `v0.1.0` → GitHub Actions → PyPI
- [ ] Show HN post: "agentvisor — ~/.ssh/agent for AI agents"
- [ ] Post in Claude Code Discord, r/LocalLLaMA
- [ ] 5 developers test GitHub MCP + Context7 flow and report back

---

## Explicitly NOT in scope

- Windows support (v1 targets macOS/Linux)
- Universal MCP auto-discovery via well-known endpoint
- Agent identity/attestation (SPIFFE/SVID)
- CI/CD support
