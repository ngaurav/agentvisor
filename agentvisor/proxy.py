import asyncio
import time
from collections.abc import AsyncIterator

import httpx
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

from . import registry, vault

_start_time = time.time()
_locks: dict[str, asyncio.Lock] = {}
_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=30.0, follow_redirects=True)
    return _client


async def _get_token(service: str) -> str:
    """Fetch vault token, retrying up to 3× on transient errors (500ms delay)."""
    for attempt in range(3):
        try:
            return vault.get(service)
        except KeyError:
            raise  # no credential — don't retry
        except Exception:
            if attempt < 2:
                await asyncio.sleep(0.5)
            else:
                raise


def _get_lock(service: str) -> asyncio.Lock:
    if service not in _locks:
        _locks[service] = asyncio.Lock()
    return _locks[service]


def _err(code: int, error: str, message: str) -> JSONResponse:
    return JSONResponse({"error": error, "message": message}, status_code=code)


async def health(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "uptime_s": int(time.time() - _start_time)})


async def status(request: Request) -> JSONResponse:
    return JSONResponse({
        "routes": registry.list_entries(),
        "uptime_s": int(time.time() - _start_time),
        "last_crash": None,
    })


async def proxy_request(request: Request) -> Response:
    path = request.url.path
    parts = path.lstrip("/").split("/", 1)
    service_name = parts[0]
    remainder = "/" + parts[1] if len(parts) > 1 else "/"

    if not service_name:
        return _err(404, "ERR_UNKNOWN_SERVICE", "No service name in path")

    try:
        entry = registry.get(service_name)
    except KeyError:
        return _err(404, "ERR_UNKNOWN_SERVICE", f"No MCP registered for '{service_name}'")

    async with _get_lock(service_name):
        try:
            token = await _get_token(service_name)
        except KeyError:
            return _err(
                503,
                "ERR_NOT_FOUND",
                f"No credential for '{service_name}'. Run: agentvisor store {service_name}",
            )
        except Exception as exc:
            return _err(503, "ERR_VAULT_LOCKED", f"Vault error: {exc}")

    upstream_url = entry["url"] + remainder
    if request.url.query:
        upstream_url += "?" + request.url.query

    headers = {k: v for k, v in request.headers.items() if k.lower() != "host"}
    headers["Authorization"] = f"Bearer {token}"

    body = await request.body()
    client = _get_client()

    try:
        req = client.build_request(
            method=request.method,
            url=upstream_url,
            headers=headers,
            content=body,
        )
        upstream_resp = await client.send(req, stream=True)
    except httpx.TimeoutException:
        return _err(504, "ERR_UPSTREAM_TIMEOUT", f"Upstream '{service_name}' timed out")
    except (httpx.ConnectError, httpx.RemoteProtocolError):
        return _err(
            503, "ERR_UPSTREAM_UNREACHABLE", f"Cannot reach upstream for '{service_name}'"
        )
    except Exception as exc:
        return _err(503, "ERR_UPSTREAM_UNREACHABLE", f"Upstream error: {exc}")

    if upstream_resp.status_code == 401:
        await upstream_resp.aclose()
        return _err(
            503,
            f"ERR_TOKEN_REJECTED:{service_name}",
            "Upstream rejected the token (401)",
        )

    resp_headers = {
        k: v
        for k, v in upstream_resp.headers.items()
        if k.lower() not in ("transfer-encoding", "content-length")
    }
    if upstream_resp.status_code == 429:
        resp_headers["X-Agentvisor-Ratelimited"] = "true"

    async def stream_body() -> AsyncIterator[bytes]:
        try:
            async for chunk in upstream_resp.aiter_bytes():
                yield chunk
        finally:
            await upstream_resp.aclose()

    return StreamingResponse(
        stream_body(),
        status_code=upstream_resp.status_code,
        headers=resp_headers,
        media_type=upstream_resp.headers.get("content-type"),
    )


def create_app() -> Starlette:
    return Starlette(
        routes=[
            Route("/health", health, methods=["GET"]),
            Route("/__agentvisor__/status", status, methods=["GET"]),
            Route(
                "/{path:path}",
                proxy_request,
                methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"],
            ),
        ]
    )
