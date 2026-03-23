# Catching up on cluefactory-ha-mcp

This is a Home Assistant MCP server that lets Claude control and manage your Home Assistant instance — automations, lights, switches, scenes, scripts, templates, and more.

The server runs as a Docker container on `slaapt.niet.io:6993` and is already deployed. You just need to point Claude at it.

## Connect Claude to the server

Edit `~/Library/Application Support/Claude/claude_desktop_config.json` and add this to the `mcpServers` section:

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

Save the file and restart Claude. That's it — the HA tools will be available in every conversation.

## What you can do

Once connected, just ask Claude things like:

- *"List all my automations"*
- *"Turn off the living room lights"*
- *"Create an automation that turns on the porch light at sunset"*
- *"Disable the holiday lights automation"*
- *"Trigger the morning routine now"*
- *"What's the current temperature from sensor.living_room?"*

## If you need to make changes to the server

The source lives at `~/Devel/cluefactory-ha-mcp`. Make changes there, commit, push to GitHub, then on the production server:

```bash
git pull
docker compose up --build -d
```

Changes to `server.py` require `--build`. Changes to `docker-compose.yml` or `.env` do not.
