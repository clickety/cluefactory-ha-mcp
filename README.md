# cluefactory-ha-mcp

A Model Context Protocol (MCP) server for Home Assistant, built around the HA REST API.
Gives Claude full automation management plus entity and service control.

## Tools

| Tool | Description |
|------|-------------|
| `ha_get_status` | Check HA API is reachable |
| `ha_get_config` | Get HA version and configuration info |
| `ha_list_states` | List entity states, filterable by domain |
| `ha_get_state` | Get state + attributes of a specific entity |
| `ha_list_services` | Browse available HA services by domain |
| `ha_call_service` | Call any HA service (lights, switches, media, etc.) |
| `ha_list_automations` | List all automations with enabled/disabled status |
| `ha_get_automation` | Get full config of a specific automation |
| `ha_create_automation` | Create a new automation |
| `ha_update_automation` | Update (replace) an existing automation |
| `ha_delete_automation` | Permanently delete an automation |
| `ha_trigger_automation` | Manually run an automation now |
| `ha_toggle_automation` | Enable or disable an automation |
| `ha_render_template` | Render a Jinja2 template via HA's engine |

## Setup

### 1. Get a Long-Lived Access Token

1. Open Home Assistant → click your username (bottom-left)
2. Scroll to **Long-Lived Access Tokens** → **Create Token**
3. Copy the token (you won't see it again)

---

### Option A: Run with Docker (recommended for network access)

The Docker image runs the server over HTTP so any system on your network can connect.

```bash
# 1. Copy and fill in your credentials
cp .env.example .env
# Edit .env with your HA_URL and HA_TOKEN

# 2. Build and start
docker compose up -d

# Server is now listening on http://your-host:8000/mcp
```

Add to your MCP client config:

```json
{
  "mcpServers": {
    "cluefactory-ha-mcp": {
      "url": "http://your-docker-host:8000/mcp"
    }
  }
}
```

---

### Option B: Run locally (stdio, single machine)

```bash
pip install -r requirements.txt

export HA_URL=http://homeassistant.local:8123
export HA_TOKEN=your_long_lived_token_here

# Add to your MCP client config:
```

```json
{
  "mcpServers": {
    "cluefactory-ha-mcp": {
      "command": "python",
      "args": ["/path/to/cluefactory-ha-mcp/server.py"],
      "env": {
        "HA_URL": "http://homeassistant.local:8123",
        "HA_TOKEN": "your_token_here"
      }
    }
  }
}
```

---

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `HA_URL` | `http://homeassistant.local:8123` | Your HA instance URL |
| `HA_TOKEN` | *(required)* | Long-Lived Access Token |
| `MCP_TRANSPORT` | `stdio` | `stdio` for local, `http` for Docker/network |
| `MCP_HOST` | `0.0.0.0` | Bind host (HTTP mode only) |
| `MCP_PORT` | `8000` | Listen port (HTTP mode only) |

## Automation IDs vs Entity IDs

Home Assistant automations have two different identifiers:

- **Entity ID** (`automation.morning_routine`) — used by `ha_list_states`, `ha_trigger_automation`, `ha_toggle_automation`
- **Config ID** (a unique string like `1620000000000`) — used by `ha_get_automation`, `ha_update_automation`, `ha_delete_automation`

Use `ha_list_automations` to see entity IDs, then `ha_get_automation` with the config ID to get full config details.

## Example Prompts

- *"List all my automations"*
- *"Show me the config for my morning routine automation"*
- *"Create an automation that turns off all lights at midnight"*
- *"Disable the holiday lights automation"*
- *"Trigger the morning routine now"*
- *"Turn on the living room light at 80% brightness"*
- *"What's the current temperature from sensor.living_room_temp?"*
