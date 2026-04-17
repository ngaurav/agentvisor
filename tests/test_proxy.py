from unittest.mock import patch

import httpx
import pytest
import respx
from starlette.testclient import TestClient

from agentvisor.proxy import create_app


@pytest.fixture()
def app(tmp_agentvisor):
    return create_app()


@pytest.fixture()
def client(app):
    return TestClient(app, raise_server_exceptions=False)


# --- Health / status ---


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert "uptime_s" in data


def test_status_empty(client):
    resp = client.get("/__agentvisor__/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["routes"] == []
    assert "uptime_s" in data
    assert data["last_crash"] is None


def test_status_with_routes(client, tmp_agentvisor):
    from agentvisor import registry

    registry.add("github", "https://api.githubcopilot.com/mcp", "pat")
    resp = client.get("/__agentvisor__/status")
    routes = resp.json()["routes"]
    assert len(routes) == 1
    assert routes[0]["name"] == "github"


# --- ERR_UNKNOWN_SERVICE ---


def test_unknown_service_returns_404(client):
    resp = client.get("/nonexistent/path")
    assert resp.status_code == 404
    assert resp.json()["error"] == "ERR_UNKNOWN_SERVICE"


# --- ERR_NOT_FOUND (no vault entry) ---


def test_no_vault_entry_returns_503(client, tmp_agentvisor):
    from agentvisor import registry

    registry.add("github", "https://api.githubcopilot.com/mcp", "pat")
    # vault entry intentionally not added
    resp = client.post("/github")
    assert resp.status_code == 503
    assert resp.json()["error"] == "ERR_NOT_FOUND"


# --- Successful proxy with Bearer injection ---


@respx.mock
def test_bearer_injection(client, tmp_agentvisor):
    from agentvisor import registry, vault

    registry.add("github", "https://api.githubcopilot.com/mcp", "pat")
    vault.store("github", "ghp_testtoken")

    mock_route = respx.post("https://api.githubcopilot.com/mcp/").mock(
        return_value=httpx.Response(200, json={"result": "ok"})
    )

    resp = client.post("/github/")
    assert resp.status_code == 200
    assert resp.json() == {"result": "ok"}

    request = mock_route.calls[0].request
    assert request.headers["authorization"] == "Bearer ghp_testtoken"


@respx.mock
def test_path_forwarding(client, tmp_agentvisor):
    from agentvisor import registry, vault

    registry.add("context7", "https://mcp.context7.com/mcp", "bearer")
    vault.store("context7", "c7key")

    mock_route = respx.post("https://mcp.context7.com/mcp/initialize").mock(
        return_value=httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": {}})
    )

    resp = client.post("/context7/initialize", json={"jsonrpc": "2.0", "method": "initialize"})
    assert resp.status_code == 200
    assert mock_route.called


# --- ERR_TOKEN_REJECTED (upstream 401) ---


@respx.mock
def test_upstream_401_returns_503(client, tmp_agentvisor):
    from agentvisor import registry, vault

    registry.add("github", "https://api.githubcopilot.com/mcp", "pat")
    vault.store("github", "ghp_badtoken")

    respx.post("https://api.githubcopilot.com/mcp/").mock(
        return_value=httpx.Response(401, json={"error": "unauthorized"})
    )

    resp = client.post("/github/")
    assert resp.status_code == 503
    assert "ERR_TOKEN_REJECTED" in resp.json()["error"]


# --- ERR_UPSTREAM_TIMEOUT ---


@respx.mock
def test_upstream_timeout_returns_504(client, tmp_agentvisor):
    from agentvisor import registry, vault

    registry.add("github", "https://api.githubcopilot.com/mcp", "pat")
    vault.store("github", "ghp_token")

    respx.post("https://api.githubcopilot.com/mcp/").mock(
        side_effect=httpx.TimeoutException("timed out")
    )

    resp = client.post("/github/")
    assert resp.status_code == 504
    assert resp.json()["error"] == "ERR_UPSTREAM_TIMEOUT"


# --- ERR_UPSTREAM_UNREACHABLE ---


@respx.mock
def test_upstream_unreachable_returns_503(client, tmp_agentvisor):
    from agentvisor import registry, vault

    registry.add("github", "https://api.githubcopilot.com/mcp", "pat")
    vault.store("github", "ghp_token")

    respx.post("https://api.githubcopilot.com/mcp/").mock(
        side_effect=httpx.ConnectError("connection refused")
    )

    resp = client.post("/github/")
    assert resp.status_code == 503
    assert resp.json()["error"] == "ERR_UPSTREAM_UNREACHABLE"


# --- 429 pass-through with header ---


@respx.mock
def test_rate_limit_adds_header(client, tmp_agentvisor):
    from agentvisor import registry, vault

    registry.add("github", "https://api.githubcopilot.com/mcp", "pat")
    vault.store("github", "ghp_token")

    respx.post("https://api.githubcopilot.com/mcp/").mock(
        return_value=httpx.Response(429, json={"error": "rate limited"})
    )

    resp = client.post("/github/")
    assert resp.status_code == 429
    assert resp.headers.get("x-agentvisor-ratelimited") == "true"


# --- Query string forwarding ---


@respx.mock
def test_query_string_forwarded(client, tmp_agentvisor):
    from agentvisor import registry, vault

    registry.add("github", "https://api.githubcopilot.com/mcp", "pat")
    vault.store("github", "ghp_token")

    mock_route = respx.get("https://api.githubcopilot.com/mcp/tools?version=2").mock(
        return_value=httpx.Response(200, json=[])
    )

    resp = client.get("/github/tools?version=2")
    assert resp.status_code == 200
    assert mock_route.called
