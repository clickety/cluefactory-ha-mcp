# Session Summary

## What we built

A Home Assistant MCP server called `cluefactory-ha-mcp` that wraps the HA REST API and exposes 14 tools to Claude:

- **States**: list entities, get entity state
- **Services**: list services, call any service
- **Automations**: list, get config, create, update, delete, trigger, toggle
- **Utilities**: render Jinja2 templates, get HA config, check API status

The server is written in Python using FastMCP, runs in Docker, and communicates over streamable HTTP so any MCP client on the network can connect.

## Where things live

- **Source**: `~/Devel/cluefactory-ha-mcp/` on your Mac, pushed to GitHub as `cluefactory-ha-mcp`
- **Production**: running as Docker on `slaapt.niet.io:6993`
- **MCP endpoint**: `http://slaapt.niet.io:6993/mcp`

## Current status

The server deploys and runs fine. We're still working on getting `mcp-remote` (the bridge Claude Desktop uses to reach HTTP MCP servers) to connect successfully. The issue is FastMCP's security middleware dropping connections before sending any response. The latest fix in the branch adds:
- `IPAllowlistMiddleware` — CIDR-based IP filtering via `MCP_ALLOWED_NETWORKS` env var (e.g. `192.168.178.0/24`)
- FastMCP's built-in host/origin restrictions opened up (since we're handling access control ourselves)
- `stateless_http=True` for cleaner OAuth discovery handling

This fix **has not been deployed yet** — it needs to be committed, pushed, and `docker compose up --build -d` run on the server.

## Claude Desktop config

In `~/Library/Application Support/Claude/claude_desktop_config.json`, add to `mcpServers`:

```json
"cluefactory-ha-mcp": {
  "command": "/opt/homebrew/bin/npx",
  "args": [
    "-y",
    "mcp-remote",
    "http://slaapt.niet.io:6993/mcp",
    "--allow-http"
  ]
}
```

## Key files

| File | Purpose |
|------|---------|
| `server.py` | The MCP server — all tools and HTTP entry point |
| `Dockerfile` | Builds the container from `python:3.12-slim` |
| `docker-compose.yml` | Runs the container, reads config from `.env` |
| `.env` | Your secrets and config — **not in git** |
| `.env.example` | Template for `.env` |

## Environment variables (in `.env` on the server)

| Variable | Example | Notes |
|----------|---------|-------|
| `HA_URL` | `http://homeassistant.local:8123` | No trailing slash |
| `HA_TOKEN` | `eyJ...` | Long-Lived Access Token from HA profile |
| `MCP_PORT` | `6993` | Host port Docker exposes |
| `MCP_ALLOWED_NETWORKS` | `192.168.178.0/24` | CIDR allowlist for incoming connections |

## Deploy workflow

```bash
# On your Mac — make changes then:
git add <files>
git commit -m "your message"
git push

# On the production server:
git pull
docker compose up --build -d   # --build only needed when server.py changes
```
