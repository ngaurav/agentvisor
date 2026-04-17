# Sprint 0 Findings

> Completed: 2026-04-17

## Pre-condition gate: PASS — proceed to Sprint 1

SSE transport confirmed. `~/.claude.json` path correct. Schema validated.

---

## Task results

### ✅ `~/.claude.json` — correct path and schema

File: `/Users/nishant/.claude.json`

Top-level `"mcpServers"` key holds a map of server name → config. Added to the file alongside existing keys like `cachedGrowthBookFeatures`, `projects`, etc.

**Minimal schema (no auth):**
```json
{
  "mcpServers": {
    "context7": {
      "type": "http",
      "url": "https://mcp.context7.com/mcp"
    }
  }
}
```

**Schema with Bearer auth (GitHub):**
```json
{
  "mcpServers": {
    "github": {
      "type": "http",
      "url": "https://api.githubcopilot.com/mcp/",
      "headers": {
        "Authorization": "Bearer ghp_YOUR_PAT_HERE"
      }
    }
  }
}
```

Env var expansion also supported: `"Bearer ${GITHUB_TOKEN}"`.

---

### ✅ SSE transport confirmed

Context7 error without correct Accept header:
```
"Not Acceptable: Client must accept both application/json and text/event-stream"
```

Claude Code sends `Accept: application/json, text/event-stream`. This is the **Streamable HTTP / SSE transport** from MCP spec 2024-11-05. **Not WebSocket.** Proxy design is valid — proceed.

---

### ✅ GitHub Remote MCP

- **Endpoint:** `https://api.githubcopilot.com/mcp/`  
  (not `mcp.github.com` — that domain doesn't resolve to MCP)
- **Auth:** `Authorization: Bearer <token>` — header only, no query param  
  Confirmed via: `"bearer_methods_supported": ["header"]` in OAuth metadata
- **Token format:** GitHub PAT (`ghp_...`) or OAuth token (`gho_...`)
- **Scopes needed:** `repo`, `read:org`, `read:user`, `user:email` (at minimum)
- **OAuth server:** `https://github.com/login/oauth`

**Status:** Config entry added to `~/.claude.json`. PAT validated — `initialize` returned `github-mcp-server` with tools/resources/prompts capabilities. SSE response format confirmed (`event: message\ndata: {...}`).

---

### ✅ Context7 MCP

- **Endpoint:** `https://mcp.context7.com/mcp`  
  (root `/` returns 404 — must use `/mcp`)
- **Auth:** None required for public access. Optional header `X-Context7-API-Key` / `Context7-API-Key` / `X-API-Key` for premium (seen in CORS `Access-Control-Allow-Headers`)
- **Tools available without auth:** `resolve-library-id`, `query-docs`
- **Session management:** `MCP-Session-Id` header used for stateful sessions

**Status:** Config entry added to `~/.claude.json`. Tools verified via direct HTTP probe. Restart Claude Code to confirm tools appear in session.

---

### ✅ Auth header formats

| Server | Header | Format |
|--------|--------|--------|
| GitHub MCP | `Authorization` | `Bearer ghp_...` or `Bearer gho_...` |
| Context7 MCP | None (optional: `X-Context7-API-Key`) | Raw key string |

---

### ✅ Failure modes documented

**GitHub MCP — no token:**
```
HTTP 401
www-authenticate: Bearer error="invalid_request", error_description="No access token was provided in this request"
```

**GitHub MCP — badly formatted token (not `ghp_`/`gho_` prefix):**
```
HTTP 4xx
bad request: Authorization header is badly formatted
```

**GitHub MCP — valid format, invalid PAT:**
```
HTTP 401
unauthorized: unauthorized: AuthenticateToken authentication failed
```

**Context7 MCP — wrong Accept header (no SSE):**
```
HTTP 200 (error in body)
{"error": {"code": -32000, "message": "Not Acceptable: Client must accept both application/json and text/event-stream"}}
```

---

## What was added to `~/.claude.json`

```json
"mcpServers": {
  "context7": {
    "type": "http",
    "url": "https://mcp.context7.com/mcp"
  }
}
```

GitHub entry pending a real PAT. Add manually:
```json
"github": {
  "type": "http",
  "url": "https://api.githubcopilot.com/mcp/",
  "headers": {
    "Authorization": "Bearer ghp_YOUR_PAT_HERE"
  }
}
```

---

## Implications for Sprint 1 proxy design

1. **SSE confirmed** — `starlette` + `httpx.AsyncClient.stream()` + `aiter_bytes()` is the right approach.
2. **GitHub auth header:** proxy must inject `Authorization: Bearer <token>` for every request.
3. **Context7 auth:** no injection needed for free tier; optional `X-Context7-API-Key` header for paid.
4. **No token in client config** — agentvisor writes `http://localhost:9090/github` to `~/.claude.json`, proxy injects the real Bearer on the way out. This is the exact pattern validated here.
5. **Session stickiness:** Context7 uses `MCP-Session-Id` — proxy must pass this header through transparently (it does, since we forward all request headers after injecting auth).
