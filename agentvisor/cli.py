import asyncio
import sys

import click


@click.group()
def cli() -> None:
    """agentvisor — ~/.ssh/agent for AI agents."""


@cli.command()
@click.argument("service")
@click.option("--overwrite", is_flag=True, help="Overwrite an existing credential.")
@click.option("--secret-stdin", is_flag=True, help="Read secret from stdin (for scripting).")
def store(service: str, overwrite: bool, secret_stdin: bool) -> None:
    """Store a credential for SERVICE."""
    from . import vault

    if secret_stdin:
        secret = sys.stdin.readline().rstrip("\n")
    else:
        secret = click.prompt(f"Secret for {service}", hide_input=True)

    try:
        vault.store(service, secret, overwrite=overwrite)
        click.echo(f"Stored credential for '{service}'")
    except ValueError as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)


@cli.command()
@click.argument("service")
def get(service: str) -> None:
    """Print the stored credential for SERVICE to stdout."""
    from . import vault

    try:
        click.echo(vault.get(service))
    except KeyError as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)


@cli.command("list")
def list_cmd() -> None:
    """List all stored credentials."""
    from . import vault

    creds = vault.list_credentials()
    if not creds:
        click.echo("No credentials stored.")
        return
    for c in creds:
        click.echo(c["service"])


@cli.command()
@click.argument("service")
def revoke(service: str) -> None:
    """Delete the credential for SERVICE."""
    from . import vault

    try:
        vault.revoke(service)
        click.echo(f"Revoked credential for '{service}'")
    except KeyError as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)


@cli.command("mcp-add")
@click.argument("name")
@click.option("--url", required=True, help="Remote MCP server URL.")
@click.option(
    "--auth",
    "auth_type",
    required=True,
    type=click.Choice(["bearer", "pat", "oauth-refresh"]),
    help="Auth injection type.",
)
@click.option("--overwrite", is_flag=True, help="Overwrite an existing entry.")
def mcp_add(name: str, url: str, auth_type: str, overwrite: bool) -> None:
    """Register an MCP server under NAME."""
    from . import registry

    try:
        registry.add(name, url, auth_type, overwrite=overwrite)
        click.echo(f"Registered MCP '{name}' → {url} (auth: {auth_type})")
    except (ValueError, KeyError) as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)


@cli.command("mcp-list")
def mcp_list() -> None:
    """List all registered MCP servers."""
    from . import registry

    entries = registry.list_entries()
    if not entries:
        click.echo("No MCP servers registered.")
        return
    for e in entries:
        click.echo(f"{e['name']:<20} {e['url']}  ({e['auth_type']})")


@cli.command("mcp-remove")
@click.argument("name")
def mcp_remove(name: str) -> None:
    """Remove the registered MCP server NAME."""
    from . import registry

    try:
        registry.remove(name)
        click.echo(f"Removed MCP '{name}'")
    except KeyError as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)


@cli.command("mcp-config")
@click.option("--proxy", "mode", flag_value="proxy", help="Write proxy entries + install OS service.")
@click.option("--unproxy", "mode", flag_value="unproxy", help="Remove proxy entries + uninstall OS service.")
@click.option("--stdout", "mode", flag_value="stdout", help="Print config snippet to stdout (no writes).")
@click.option("--out", "out_path", default=None, metavar="PATH", help="Write config to PATH (no service install).")
@click.option("--port", default=9090, show_default=True, type=int, help="Proxy port.")
def mcp_config(mode: str | None, out_path: str | None, port: int) -> None:
    """Configure Claude Code to route registered MCPs through the agentvisor proxy.

    \b
    Examples:
      agentvisor mcp-config --proxy          # write config + install OS service
      agentvisor mcp-config --unproxy        # remove config + uninstall OS service
      agentvisor mcp-config --stdout         # preview JSON snippet
      agentvisor mcp-config --proxy --out /tmp/claude.json
    """
    import json
    from pathlib import Path

    from . import config, registry, service

    if mode is None and out_path is None:
        click.echo(
            "Specify --proxy, --unproxy, --stdout, or --out PATH. Run with --help for usage.",
            err=True,
        )
        sys.exit(1)

    entries = registry.list_entries()

    if mode == "stdout":
        click.echo(config.render(entries, port))
        return

    if out_path is not None:
        data: dict = {}
        p = Path(out_path)
        if p.exists():
            try:
                data = json.loads(p.read_text())
            except Exception:
                pass
        mcp_servers = data.setdefault("mcpServers", {})
        for entry in entries:
            mcp_servers[entry["name"]] = {
                "type": "http",
                "url": config.proxy_url(entry["name"], port),
            }
        p.write_text(json.dumps(data, indent=2))
        click.echo(f"Wrote {len(entries)} MCP entries to {out_path}")
        return

    if mode == "proxy":
        if not entries:
            click.echo(
                "No MCP servers registered. Run: agentvisor mcp-add <name> --url ... --auth ...",
                err=True,
            )
            sys.exit(1)
        try:
            config.upsert_with_service(entries, port, lambda: service.install(port=port))
        except RuntimeError as exc:
            click.echo(f"Error: service install failed — {exc}", err=True)
            click.echo("~/.claude.json rolled back.", err=True)
            sys.exit(1)
        click.echo(f"Wrote {len(entries)} MCP entries to ~/.claude.json")
        click.echo(f"OS service installed: {service.PLIST_PATH}")
        click.echo("Restart Claude Code to pick up changes.")

    elif mode == "unproxy":
        removed = config.remove_all_managed()
        service.uninstall()
        if removed:
            click.echo(f"Removed config entries: {', '.join(removed)}")
        else:
            click.echo("No managed entries found in ~/.claude.json")
        click.echo("OS service uninstalled.")
        click.echo("Restart Claude Code to pick up changes.")


@cli.command("uninstall")
@click.option("--keep-vault", is_flag=True, help="Keep vault and registry; only remove service + config.")
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation prompt.")
def uninstall(keep_vault: bool, yes: bool) -> None:
    """Fully remove agentvisor: stop proxy, remove OS service, remove Claude config entries.

    \b
    By default also deletes ~/.agentvisor/ (vault, registry, logs).
    Pass --keep-vault to preserve credentials and re-use them later.

    \b
    Validation: no residue in ~/.claude.json or ~/Library/LaunchAgents/ afterward.
    """
    import shutil

    from . import config, service

    if not yes:
        click.echo("This will:")
        click.echo("  • Stop the proxy (background process + OS service)")
        click.echo("  • Remove agentvisor entries from ~/.claude.json")
        if not keep_vault:
            click.echo("  • Delete ~/.agentvisor/ (vault, registry, logs)")
        else:
            click.echo("  • Keep ~/.agentvisor/ intact (--keep-vault)")
        click.confirm("\nContinue?", abort=True)

    # 1. Stop background proxy (ignore if not running)
    try:
        service.stop_background()
        click.echo("Stopped background proxy.")
    except RuntimeError:
        pass

    # 2. Uninstall OS service
    service.uninstall()
    click.echo(f"OS service removed ({service.PLIST_PATH.name}).")

    # 3. Remove Claude config entries
    removed = config.remove_all_managed()
    if removed:
        click.echo(f"Removed config entries: {', '.join(removed)}")
    else:
        click.echo("No managed config entries found in ~/.claude.json.")

    # 4. Optionally delete ~/.agentvisor/
    if not keep_vault:
        if service.AGENTVISOR_DIR.exists():
            shutil.rmtree(service.AGENTVISOR_DIR)
            click.echo("Deleted ~/.agentvisor/")

    click.echo("\nDone. Restart Claude Code to pick up config changes.")


@cli.command("proxy-start")
@click.option("--background", is_flag=True, help="Start detached (background).")
@click.option("--host", default="127.0.0.1", show_default=True, help="Bind address.")
@click.option("--port", default=9090, show_default=True, type=int, help="Listen port.")
def proxy_start(background: bool, host: str, port: int) -> None:
    """Start the auth-injecting proxy (Ctrl-C to stop when running in foreground)."""
    if background:
        from . import service

        try:
            pid = service.start_background(host, port)
            click.echo(f"Started agentvisor proxy in background (PID {pid})")
            click.echo(f"Logs: {service.LOG_DIR / 'proxy.log'}")
        except RuntimeError as exc:
            click.echo(f"Error: {exc}", err=True)
            sys.exit(1)
    else:
        import uvicorn

        from .proxy import create_app

        click.echo(f"Starting agentvisor proxy on {host}:{port}")
        app = create_app()
        cfg = uvicorn.Config(app, host=host, port=port, log_level="info")
        server = uvicorn.Server(cfg)
        asyncio.run(server.serve())


@cli.command("proxy-stop")
def proxy_stop() -> None:
    """Stop the background proxy process."""
    from . import service

    try:
        service.stop_background()
        click.echo("Proxy stopped.")
    except RuntimeError as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)


@cli.command("proxy-status")
@click.option("--port", default=9090, show_default=True, type=int, help="Proxy port.")
def proxy_status(port: int) -> None:
    """Show proxy status and registered MCP routes. Self-sufficient for debugging a failed connection."""
    import httpx

    from . import registry, service, vault

    # ── process / service ──────────────────────────────────────────────────────
    pid = service.background_pid()
    svc_installed = service.is_installed()

    if pid:
        click.echo(f"Process   running (PID {pid})")
    elif svc_installed and service.is_running():
        click.echo(f"Service   running  ({service.PLIST_PATH.name})")
    elif svc_installed:
        click.echo(f"Service   INSTALLED BUT NOT RUNNING  ({service.PLIST_PATH})")
        click.echo("          → launchctl load " + str(service.PLIST_PATH))
    else:
        click.echo("Process   not running")
        click.echo("          → agentvisor mcp-config --proxy   (installs + starts)")

    # ── HTTP health ────────────────────────────────────────────────────────────
    try:
        resp = httpx.get(f"http://127.0.0.1:{port}/health", timeout=2.0)
        data = resp.json()
        click.echo(f"HTTP      reachable on :{port}  (uptime {data.get('uptime_s', '?')}s)")
    except Exception:
        click.echo(f"HTTP      UNREACHABLE on 127.0.0.1:{port}")
        click.echo("          → check logs: cat ~/.agentvisor/logs/proxy.log")

    # ── routes ─────────────────────────────────────────────────────────────────
    entries = registry.list_entries()
    if not entries:
        click.echo("\nRoutes    none registered")
        click.echo("          → agentvisor mcp-add <name> --url <url> --auth bearer|pat|oauth-refresh")
        return

    click.echo(f"\n{'Route':<22} {'Upstream':<45} Auth         Vault")
    click.echo("─" * 100)
    for e in entries:
        try:
            vault.get(e["name"])
            cred = "ok"
        except KeyError:
            cred = f"MISSING — run: agentvisor store {e['name']}"
        except Exception:
            cred = "ERROR (vault locked?)"

        route = f"/{e['name']}"
        click.echo(f"{route:<22} {e['url']:<45} {e['auth_type']:<12} {cred}")
