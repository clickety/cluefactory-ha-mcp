#!/usr/bin/env python3
"""
cluefactory-ha-mcp: Home Assistant MCP Server

Version: 1.1.4


Provides full automation management and entity control for Home Assistant
via the HA REST API. Requires a Long-Lived Access Token and your HA URL.

Environment variables:
  HA_URL              - Base URL of your HA instance (e.g. http://homeassistant.local:8123)
  HA_TOKEN            - Long-Lived Access Token from your HA user profile
  MCP_TRANSPORT       - Transport mode: 'stdio' (default, local) or 'http' (network/Docker)
  MCP_HOST            - Host to bind when using HTTP transport (default: 0.0.0.0)
  MCP_PORT            - Port to listen on when using HTTP transport (default: 8000)
  MCP_ALLOWED_NETWORKS - Comma-separated CIDRs allowed to connect (default: 0.0.0.0/0)
                         Example: 192.168.178.0/24,10.0.0.0/8
  MCP_SSL_DIR          - Directory containing cert.pem and privkey.pem for HTTPS.
                         If unset, server runs plain HTTP.
                         Example: /etc/letsencrypt/live/yourdomain.com
"""

import json
import os
import re
import sys
import uuid
from enum import Enum
from ipaddress import AddressValueError, IPv4Address, IPv6Address, ip_network
from typing import Any, Dict, List, Optional

import httpx
import uvicorn
from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field, field_validator
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

# ---------------------------------------------------------------------------
# Server initialisation
# ---------------------------------------------------------------------------

mcp = FastMCP("ha_mcp")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

HA_URL: str = os.environ.get("HA_URL", "http://homeassistant.local:8123").rstrip("/")
HA_TOKEN: str = os.environ.get("HA_TOKEN", "")

if not HA_TOKEN:
    print(
        "WARNING: HA_TOKEN environment variable is not set. "
        "All API calls will fail with 401 Unauthorized.",
        file=sys.stderr,
    )

# ---------------------------------------------------------------------------
# Shared utilities
# ---------------------------------------------------------------------------


def _headers() -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {HA_TOKEN}",
        "Content-Type": "application/json",
    }


async def _request(
    method: str,
    path: str,
    json_body: Optional[Dict[str, Any]] = None,
    params: Optional[Dict[str, Any]] = None,
) -> Any:
    """Central HTTP client for all HA API calls."""
    url = f"{HA_URL}/api{path}"
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.request(
            method,
            url,
            headers=_headers(),
            json=json_body,
            params=params,
        )
        response.raise_for_status()
        if response.content:
            return response.json()
        return {}


def _handle_error(e: Exception) -> str:
    """Return a clear, actionable error message."""
    if isinstance(e, httpx.HTTPStatusError):
        status = e.response.status_code
        if status == 401:
            return (
                "Error: Unauthorized (401). Check that HA_TOKEN is set correctly "
                "and is a valid Long-Lived Access Token."
            )
        if status == 403:
            return "Error: Forbidden (403). Your token does not have permission for this action."
        if status == 404:
            return (
                "Error: Not found (404). The entity or resource does not exist. "
                "Double-check the entity_id or automation ID."
            )
        if status == 405:
            return "Error: Method not allowed (405). This endpoint may not support this operation."
        try:
            detail = e.response.json()
            return f"Error: HTTP {status} — {detail}"
        except Exception:
            return f"Error: HTTP {status} — {e.response.text[:300]}"
    if isinstance(e, httpx.TimeoutException):
        return "Error: Request timed out. Is Home Assistant reachable at the configured HA_URL?"
    if isinstance(e, httpx.ConnectError):
        return (
            f"Error: Could not connect to Home Assistant at {HA_URL}. "
            "Check HA_URL is correct and HA is running."
        )
    return f"Error: Unexpected error — {type(e).__name__}: {e}"


# ---------------------------------------------------------------------------
# Enums and shared input models
# ---------------------------------------------------------------------------


class ResponseFormat(str, Enum):
    MARKDOWN = "markdown"
    JSON = "json"


# ---------------------------------------------------------------------------
# Tool: ha_get_status
# ---------------------------------------------------------------------------


@mcp.tool(
    name="ha_get_status",
    annotations={
        "title": "Get Home Assistant API Status",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def ha_get_status() -> str:
    """Check that the Home Assistant REST API is reachable and responding.

    Returns a simple status message confirming the API is running, or an error
    if HA is unreachable or the token is invalid.

    Returns:
        str: Status message, e.g. "API running." or an error string.

    Examples:
        - Use when: You want to verify HA is reachable before other operations.
    """
    try:
        data = await _request("GET", "/")
        return data.get("message", "API running.")
    except Exception as e:
        return _handle_error(e)


# ---------------------------------------------------------------------------
# Tool: ha_get_config
# ---------------------------------------------------------------------------


@mcp.tool(
    name="ha_get_config",
    annotations={
        "title": "Get Home Assistant Configuration",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def ha_get_config() -> str:
    """Get the current Home Assistant configuration summary.

    Returns information about the HA instance: version, location, unit system,
    enabled components, and more.

    Returns:
        str: JSON-formatted HA configuration object.

    Examples:
        - Use when: You need to know the HA version or which components are loaded.
    """
    try:
        data = await _request("GET", "/config")
        return json.dumps(data, indent=2)
    except Exception as e:
        return _handle_error(e)


# ---------------------------------------------------------------------------
# Tool: ha_list_states
# ---------------------------------------------------------------------------


class ListStatesInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True, extra="forbid")

    domain: Optional[str] = Field(
        default=None,
        description=(
            "Filter entities by domain (e.g. 'automation', 'light', 'switch', 'sensor'). "
            "If omitted, all entities are returned."
        ),
    )
    limit: Optional[int] = Field(
        default=50,
        description="Maximum number of entities to return (default 50, max 500).",
        ge=1,
        le=500,
    )
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN,
        description="Output format: 'markdown' for human-readable, 'json' for machine-readable.",
    )


@mcp.tool(
    name="ha_list_states",
    annotations={
        "title": "List Home Assistant Entity States",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def ha_list_states(params: ListStatesInput) -> str:
    """List entity states from Home Assistant, optionally filtered by domain.

    Returns a list of entities with their current state and key attributes.
    Use domain='automation' to list automations, domain='light' for lights, etc.

    Args:
        params (ListStatesInput):
            - domain (Optional[str]): Domain filter, e.g. 'automation', 'light'
            - limit (Optional[int]): Max results to return (default 50)
            - response_format: 'markdown' or 'json'

    Returns:
        str: Formatted list of entity states.

    Examples:
        - "List all automations" → domain='automation'
        - "Show me all lights" → domain='light'
        - "What sensors do I have?" → domain='sensor'
    """
    try:
        states: List[Dict[str, Any]] = await _request("GET", "/states")

        if params.domain:
            states = [s for s in states if s["entity_id"].startswith(f"{params.domain}.")]

        states = states[: params.limit]

        if not states:
            domain_info = f" in domain '{params.domain}'" if params.domain else ""
            return f"No entities found{domain_info}."

        if params.response_format == ResponseFormat.JSON:
            return json.dumps(
                {
                    "count": len(states),
                    "domain_filter": params.domain,
                    "states": states,
                },
                indent=2,
            )

        # Markdown
        lines = [f"## Entity States{f' — {params.domain}' if params.domain else ''}", ""]
        lines.append(f"Showing {len(states)} entities\n")
        for s in states:
            entity_id = s["entity_id"]
            state = s["state"]
            attrs = s.get("attributes", {})
            friendly = attrs.get("friendly_name", "")
            label = f"{friendly} (`{entity_id}`)" if friendly else f"`{entity_id}`"
            lines.append(f"- **{label}**: {state}")
        return "\n".join(lines)

    except Exception as e:
        return _handle_error(e)


# ---------------------------------------------------------------------------
# Tool: ha_get_state
# ---------------------------------------------------------------------------


class GetStateInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True, extra="forbid")

    entity_id: str = Field(
        ...,
        description="Full entity ID, e.g. 'automation.morning_routine' or 'light.living_room'.",
        min_length=3,
    )
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN,
        description="Output format: 'markdown' or 'json'.",
    )


@mcp.tool(
    name="ha_get_state",
    annotations={
        "title": "Get State of a Home Assistant Entity",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def ha_get_state(params: GetStateInput) -> str:
    """Get the current state and all attributes of a specific HA entity.

    Args:
        params (GetStateInput):
            - entity_id (str): Full entity ID (e.g. 'light.living_room')
            - response_format: 'markdown' or 'json'

    Returns:
        str: Entity state and attributes.

    Examples:
        - "What state is automation.morning_routine in?" → entity_id='automation.morning_routine'
        - "Is the kitchen light on?" → entity_id='light.kitchen'
    """
    try:
        data = await _request("GET", f"/states/{params.entity_id}")

        if params.response_format == ResponseFormat.JSON:
            return json.dumps(data, indent=2)

        attrs = data.get("attributes", {})
        friendly = attrs.get("friendly_name", params.entity_id)
        lines = [
            f"## {friendly}",
            f"- **Entity ID**: `{data['entity_id']}`",
            f"- **State**: {data['state']}",
            f"- **Last changed**: {data.get('last_changed', 'unknown')}",
            f"- **Last updated**: {data.get('last_updated', 'unknown')}",
            "",
            "### Attributes",
        ]
        for k, v in attrs.items():
            if k != "friendly_name":
                lines.append(f"- **{k}**: {v}")
        return "\n".join(lines)

    except Exception as e:
        return _handle_error(e)


# ---------------------------------------------------------------------------
# Tool: ha_list_services
# ---------------------------------------------------------------------------


class ListServicesInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True, extra="forbid")

    domain: Optional[str] = Field(
        default=None,
        description=(
            "Filter services by domain (e.g. 'automation', 'light', 'switch'). "
            "If omitted, all domains are returned."
        ),
    )


@mcp.tool(
    name="ha_list_services",
    annotations={
        "title": "List Available Home Assistant Services",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def ha_list_services(params: ListServicesInput) -> str:
    """List available Home Assistant services, optionally filtered by domain.

    Services are actions you can call (e.g. light.turn_on, automation.trigger).
    Use this to discover what services and their parameters are available.

    Args:
        params (ListServicesInput):
            - domain (Optional[str]): Filter by domain (e.g. 'automation', 'light')

    Returns:
        str: Formatted list of services and their descriptions.

    Examples:
        - "What automation services are available?" → domain='automation'
        - "List light services" → domain='light'
    """
    try:
        data: List[Dict[str, Any]] = await _request("GET", "/services")

        if params.domain:
            data = [d for d in data if d.get("domain") == params.domain]

        if not data:
            return f"No services found{f' for domain {params.domain}' if params.domain else ''}."

        lines = [f"## Available Services{f' — {params.domain}' if params.domain else ''}", ""]
        for domain_entry in data:
            domain_name = domain_entry.get("domain", "unknown")
            services = domain_entry.get("services", {})
            lines.append(f"### {domain_name}")
            for svc_name, svc_info in services.items():
                desc = svc_info.get("description", "No description")
                lines.append(f"- **{domain_name}.{svc_name}**: {desc}")
            lines.append("")
        return "\n".join(lines)

    except Exception as e:
        return _handle_error(e)


# ---------------------------------------------------------------------------
# Tool: ha_call_service
# ---------------------------------------------------------------------------


class CallServiceInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True, extra="forbid")

    domain: str = Field(
        ...,
        description="Service domain, e.g. 'light', 'switch', 'automation', 'script'.",
        min_length=1,
    )
    service: str = Field(
        ...,
        description="Service name, e.g. 'turn_on', 'turn_off', 'trigger', 'toggle'.",
        min_length=1,
    )
    service_data: Optional[Dict[str, Any]] = Field(
        default=None,
        description=(
            "Optional service data as a JSON object. "
            "E.g. {\"entity_id\": \"light.living_room\", \"brightness\": 128}"
        ),
    )


@mcp.tool(
    name="ha_call_service",
    annotations={
        "title": "Call a Home Assistant Service",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def ha_call_service(params: CallServiceInput) -> str:
    """Call any Home Assistant service with optional service data.

    This is the primary way to control HA entities: turn lights on/off,
    trigger automations, run scripts, activate scenes, etc.

    Args:
        params (CallServiceInput):
            - domain (str): Service domain (e.g. 'light', 'automation')
            - service (str): Service name (e.g. 'turn_on', 'trigger')
            - service_data (Optional[dict]): Parameters for the service call,
              including entity_id and any service-specific fields

    Returns:
        str: Confirmation message or list of affected entity states.

    Examples:
        - Turn on a light: domain='light', service='turn_on',
          service_data={"entity_id": "light.living_room", "brightness": 200}
        - Trigger an automation: domain='automation', service='trigger',
          service_data={"entity_id": "automation.morning_routine"}
        - Activate a scene: domain='scene', service='turn_on',
          service_data={"entity_id": "scene.evening"}
    """
    try:
        result = await _request(
            "POST",
            f"/services/{params.domain}/{params.service}",
            json_body=params.service_data or {},
        )
        if isinstance(result, list) and result:
            entity_ids = [s.get("entity_id", "unknown") for s in result]
            return f"Service `{params.domain}.{params.service}` called successfully. Affected entities: {', '.join(entity_ids)}"
        return f"Service `{params.domain}.{params.service}` called successfully."
    except Exception as e:
        return _handle_error(e)


# ---------------------------------------------------------------------------
# Tool: ha_list_automations
# ---------------------------------------------------------------------------


class ListAutomationsInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True, extra="forbid")

    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN,
        description="Output format: 'markdown' or 'json'.",
    )


@mcp.tool(
    name="ha_list_automations",
    annotations={
        "title": "List Home Assistant Automations",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def ha_list_automations(params: ListAutomationsInput) -> str:
    """List all automations in Home Assistant with their current state (on/off).

    Returns automation entity IDs, friendly names, and whether each is enabled.
    Use ha_get_automation to retrieve the full config of a specific automation.

    Args:
        params (ListAutomationsInput):
            - response_format: 'markdown' or 'json'

    Returns:
        str: List of automations with their entity IDs and enabled/disabled status.

    Examples:
        - "Show me all my automations"
        - "Which automations are currently disabled?"
    """
    try:
        states: List[Dict[str, Any]] = await _request("GET", "/states")
        automations = [s for s in states if s["entity_id"].startswith("automation.")]

        if not automations:
            return "No automations found."

        if params.response_format == ResponseFormat.JSON:
            return json.dumps(
                {
                    "count": len(automations),
                    "automations": [
                        {
                            "entity_id": s["entity_id"],
                            "friendly_name": s.get("attributes", {}).get("friendly_name", ""),
                            "state": s["state"],
                            "last_triggered": s.get("attributes", {}).get("last_triggered"),
                            "mode": s.get("attributes", {}).get("mode"),
                        }
                        for s in automations
                    ],
                },
                indent=2,
            )

        lines = [f"## Automations ({len(automations)} total)", ""]
        enabled = [s for s in automations if s["state"] == "on"]
        disabled = [s for s in automations if s["state"] != "on"]

        if enabled:
            lines.append("### ✅ Enabled")
            for s in enabled:
                name = s.get("attributes", {}).get("friendly_name", s["entity_id"])
                triggered = s.get("attributes", {}).get("last_triggered", "never")
                lines.append(f"- **{name}** (`{s['entity_id']}`) — last triggered: {triggered}")
            lines.append("")

        if disabled:
            lines.append("### ⏸ Disabled")
            for s in disabled:
                name = s.get("attributes", {}).get("friendly_name", s["entity_id"])
                lines.append(f"- **{name}** (`{s['entity_id']}`)")
            lines.append("")

        return "\n".join(lines)

    except Exception as e:
        return _handle_error(e)


# ---------------------------------------------------------------------------
# Tool: ha_get_automation
# ---------------------------------------------------------------------------


class GetAutomationInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True, extra="forbid")

    automation_id: str = Field(
        ...,
        description=(
            "The automation's unique ID (not the entity_id). "
            "This is the 'id' field in the automation config, visible in the HA UI. "
            "Example: '1620000000000' or a custom string like 'morning_routine'."
        ),
        min_length=1,
    )


@mcp.tool(
    name="ha_get_automation",
    annotations={
        "title": "Get Home Assistant Automation Config",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def ha_get_automation(params: GetAutomationInput) -> str:
    """Get the full YAML-equivalent config of a specific automation.

    Returns the complete automation definition: alias, description, triggers,
    conditions, actions, and mode. Use ha_list_automations first to find automation IDs.

    Note: This uses the automation's unique 'id' field, not the entity_id.
    The automation must be stored in automations.yaml (not defined via UI packages).

    Args:
        params (GetAutomationInput):
            - automation_id (str): The automation's unique ID

    Returns:
        str: JSON-formatted automation configuration.

    Examples:
        - "Show me the config for automation ID 1620000000000"
    """
    try:
        data = await _request("GET", f"/config/automation/config/{params.automation_id}")
        return json.dumps(data, indent=2)
    except Exception as e:
        return _handle_error(e)


# ---------------------------------------------------------------------------
# Tool: ha_create_automation
# ---------------------------------------------------------------------------


class CreateAutomationInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True, extra="forbid")

    alias: str = Field(
        ...,
        description="Human-readable name for the automation, e.g. 'Turn off lights at midnight'.",
        min_length=1,
        max_length=255,
    )
    description: Optional[str] = Field(
        default="",
        description="Optional longer description of what this automation does.",
    )
    trigger: List[Dict[str, Any]] = Field(
        ...,
        description=(
            "List of trigger definitions. Each trigger is a dict with at minimum a 'platform' key. "
            "E.g. [{\"platform\": \"time\", \"at\": \"23:00:00\"}] or "
            "[{\"platform\": \"state\", \"entity_id\": \"binary_sensor.motion\", \"to\": \"on\"}]"
        ),
    )
    condition: Optional[List[Dict[str, Any]]] = Field(
        default_factory=list,
        description=(
            "Optional list of conditions that must be met for the automation to run. "
            "E.g. [{\"condition\": \"time\", \"after\": \"22:00:00\"}]"
        ),
    )
    action: List[Dict[str, Any]] = Field(
        ...,
        description=(
            "List of actions to perform. Each action is a dict. "
            "E.g. [{\"service\": \"light.turn_off\", \"target\": {\"entity_id\": \"light.all\"}}]"
        ),
    )
    mode: Optional[str] = Field(
        default="single",
        description=(
            "Automation mode: 'single' (default), 'restart', 'queued', or 'parallel'. "
            "Controls what happens if the automation is triggered while already running."
        ),
    )


@mcp.tool(
    name="ha_create_automation",
    annotations={
        "title": "Create a New Home Assistant Automation",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def ha_create_automation(params: CreateAutomationInput) -> str:
    """Create a new automation in Home Assistant.

    The automation is saved to automations.yaml and immediately active.
    A unique ID is auto-generated by HA.

    Args:
        params (CreateAutomationInput):
            - alias (str): Human-readable automation name
            - description (Optional[str]): Description of the automation
            - trigger (List[dict]): List of trigger definitions
            - condition (Optional[List[dict]]): List of conditions (default: [])
            - action (List[dict]): List of actions to perform
            - mode (Optional[str]): Run mode — 'single', 'restart', 'queued', 'parallel'

    Returns:
        str: The ID of the newly created automation, or an error message.

    Examples:
        - Create a time-based automation to turn off lights at midnight:
          alias='Lights off at midnight',
          trigger=[{"platform": "time", "at": "00:00:00"}],
          action=[{"service": "light.turn_off", "target": {"entity_id": "all"}}]
    """
    try:
        body: Dict[str, Any] = {
            "alias": params.alias,
            "description": params.description or "",
            "trigger": params.trigger,
            "condition": params.condition or [],
            "action": params.action,
            "mode": params.mode or "single",
        }
        automation_id = str(uuid.uuid4()).replace("-", "")
        body["id"] = automation_id
        await _request("POST", f"/config/automation/config/{automation_id}", json_body=body)
        return (
            f"Automation created successfully.\n"
            f"- **Alias**: {params.alias}\n"
            f"- **ID**: `{automation_id}`\n\n"
            f"Use this ID with ha_get_automation or ha_update_automation."
        )
    except Exception as e:
        return _handle_error(e)


# ---------------------------------------------------------------------------
# Tool: ha_update_automation
# ---------------------------------------------------------------------------


class UpdateAutomationInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True, extra="forbid")

    automation_id: str = Field(
        ...,
        description="The unique ID of the automation to update (from ha_get_automation).",
        min_length=1,
    )
    alias: str = Field(
        ...,
        description="Human-readable name for the automation.",
        min_length=1,
        max_length=255,
    )
    description: Optional[str] = Field(
        default="",
        description="Optional description of the automation.",
    )
    trigger: List[Dict[str, Any]] = Field(
        ...,
        description="Complete list of trigger definitions (replaces existing triggers).",
    )
    condition: Optional[List[Dict[str, Any]]] = Field(
        default_factory=list,
        description="Complete list of conditions (replaces existing conditions).",
    )
    action: List[Dict[str, Any]] = Field(
        ...,
        description="Complete list of actions (replaces existing actions).",
    )
    mode: Optional[str] = Field(
        default="single",
        description="Automation mode: 'single', 'restart', 'queued', or 'parallel'.",
    )


@mcp.tool(
    name="ha_update_automation",
    annotations={
        "title": "Update an Existing Home Assistant Automation",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def ha_update_automation(params: UpdateAutomationInput) -> str:
    """Update (fully replace) the configuration of an existing automation.

    This performs a full replacement of the automation config. Use ha_get_automation
    first to retrieve the current config, modify it, then pass the full updated
    config here.

    Args:
        params (UpdateAutomationInput):
            - automation_id (str): The automation's unique ID
            - alias (str): New/updated name
            - description (Optional[str]): New/updated description
            - trigger (List[dict]): Complete updated trigger list
            - condition (Optional[List[dict]]): Complete updated condition list
            - action (List[dict]): Complete updated action list
            - mode (Optional[str]): Run mode

    Returns:
        str: Success confirmation or error message.

    Examples:
        - "Change the trigger time to 22:30" — fetch config with ha_get_automation,
          update the trigger, then call ha_update_automation with the full config.
    """
    try:
        body: Dict[str, Any] = {
            "alias": params.alias,
            "description": params.description or "",
            "trigger": params.trigger,
            "condition": params.condition or [],
            "action": params.action,
            "mode": params.mode or "single",
        }
        await _request(
            "POST",
            f"/config/automation/config/{params.automation_id}",
            json_body=body,
        )
        return (
            f"Automation `{params.automation_id}` updated successfully.\n"
            f"- **Alias**: {params.alias}\n"
            f"- **Triggers**: {len(params.trigger)}\n"
            f"- **Actions**: {len(params.action)}"
        )
    except Exception as e:
        return _handle_error(e)


# ---------------------------------------------------------------------------
# Tool: ha_delete_automation
# ---------------------------------------------------------------------------


class DeleteAutomationInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True, extra="forbid")

    automation_id: str = Field(
        ...,
        description="The unique ID of the automation to delete.",
        min_length=1,
    )


@mcp.tool(
    name="ha_delete_automation",
    annotations={
        "title": "Delete a Home Assistant Automation",
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def ha_delete_automation(params: DeleteAutomationInput) -> str:
    """Permanently delete an automation from Home Assistant.

    This removes the automation from automations.yaml. This action cannot be undone.
    Consider using ha_toggle_automation to disable instead of delete.

    Args:
        params (DeleteAutomationInput):
            - automation_id (str): The unique ID of the automation to delete

    Returns:
        str: Confirmation of deletion or error message.

    Examples:
        - "Delete automation with ID 1620000000000"
    """
    try:
        await _request("DELETE", f"/config/automation/config/{params.automation_id}")
        return f"Automation `{params.automation_id}` deleted successfully."
    except Exception as e:
        return _handle_error(e)


# ---------------------------------------------------------------------------
# Tool: ha_trigger_automation
# ---------------------------------------------------------------------------


class TriggerAutomationInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True, extra="forbid")

    entity_id: str = Field(
        ...,
        description="The automation's entity ID, e.g. 'automation.morning_routine'.",
        min_length=1,
    )
    skip_condition: Optional[bool] = Field(
        default=False,
        description=(
            "If True, the automation will run even if its conditions are not met. "
            "Default is False."
        ),
    )


@mcp.tool(
    name="ha_trigger_automation",
    annotations={
        "title": "Manually Trigger a Home Assistant Automation",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def ha_trigger_automation(params: TriggerAutomationInput) -> str:
    """Manually trigger an automation to run immediately.

    This fires the automation regardless of its trigger conditions (though
    normal conditions still apply unless skip_condition=True).

    Args:
        params (TriggerAutomationInput):
            - entity_id (str): The automation entity ID (e.g. 'automation.morning_routine')
            - skip_condition (Optional[bool]): Skip condition checks if True

    Returns:
        str: Confirmation message or error.

    Examples:
        - "Run the morning routine automation now"
          → entity_id='automation.morning_routine'
    """
    try:
        service_data: Dict[str, Any] = {"entity_id": params.entity_id}
        if params.skip_condition:
            service_data["skip_condition"] = True
        await _request("POST", "/services/automation/trigger", json_body=service_data)
        return f"Automation `{params.entity_id}` triggered successfully."
    except Exception as e:
        return _handle_error(e)


# ---------------------------------------------------------------------------
# Tool: ha_toggle_automation
# ---------------------------------------------------------------------------


class ToggleAutomationInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True, extra="forbid")

    entity_id: str = Field(
        ...,
        description="The automation's entity ID, e.g. 'automation.morning_routine'.",
        min_length=1,
    )
    enable: Optional[bool] = Field(
        default=None,
        description=(
            "True to enable the automation, False to disable it. "
            "If omitted, the current state is toggled."
        ),
    )


@mcp.tool(
    name="ha_toggle_automation",
    annotations={
        "title": "Enable or Disable a Home Assistant Automation",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def ha_toggle_automation(params: ToggleAutomationInput) -> str:
    """Enable, disable, or toggle a Home Assistant automation.

    Args:
        params (ToggleAutomationInput):
            - entity_id (str): The automation entity ID
            - enable (Optional[bool]): True=enable, False=disable, None=toggle

    Returns:
        str: Confirmation message.

    Examples:
        - "Disable the night mode automation"
          → entity_id='automation.night_mode', enable=False
        - "Toggle the holiday lights automation"
          → entity_id='automation.holiday_lights'
    """
    try:
        if params.enable is True:
            service = "turn_on"
            action_label = "enabled"
        elif params.enable is False:
            service = "turn_off"
            action_label = "disabled"
        else:
            service = "toggle"
            action_label = "toggled"

        await _request(
            "POST",
            f"/services/automation/{service}",
            json_body={"entity_id": params.entity_id},
        )
        return f"Automation `{params.entity_id}` {action_label} successfully."
    except Exception as e:
        return _handle_error(e)


# ---------------------------------------------------------------------------
# Tool: ha_render_template
# ---------------------------------------------------------------------------


class RenderTemplateInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True, extra="forbid")

    template: str = Field(
        ...,
        description=(
            "Jinja2 template string to render. "
            "E.g. '{{ states(\"sensor.temperature\") }}' or "
            "'{% if is_state(\"light.lounge\", \"on\") %}On{% else %}Off{% endif %}'"
        ),
        min_length=1,
    )


@mcp.tool(
    name="ha_render_template",
    annotations={
        "title": "Render a Home Assistant Jinja2 Template",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def ha_render_template(params: RenderTemplateInput) -> str:
    """Render a Jinja2 template using Home Assistant's template engine.

    Useful for previewing templates before using them in automations,
    or for querying complex state expressions.

    Args:
        params (RenderTemplateInput):
            - template (str): Jinja2 template string

    Returns:
        str: The rendered output of the template.

    Examples:
        - "What does {{ states('sensor.temperature') }} return?"
        - Test a condition template before using it in an automation
    """
    try:
        result = await _request("POST", "/template", json_body={"template": params.template})
        if isinstance(result, str):
            return result
        return json.dumps(result, indent=2)
    except Exception as e:
        return _handle_error(e)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# IP allowlist middleware
# ---------------------------------------------------------------------------

class IPAllowlistMiddleware(BaseHTTPMiddleware):
    """Reject connections from IPs not in the configured CIDR allowlist."""

    def __init__(self, app, allowed_networks: List[str]) -> None:
        super().__init__(app)
        self.networks = [ip_network(n.strip(), strict=False) for n in allowed_networks]

    async def dispatch(self, request: Request, call_next) -> Response:
        client_host = request.client.host if request.client else None
        if client_host:
            try:
                addr = IPv4Address(client_host)
            except AddressValueError:
                try:
                    addr = IPv6Address(client_host)
                except AddressValueError:
                    return Response("Forbidden", status_code=403)
            if not any(addr in net for net in self.networks):
                print(f"Rejected connection from {client_host} (not in allowed networks)", file=sys.stderr)
                return Response("Forbidden", status_code=403)
        return await call_next(request)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    transport = os.environ.get("MCP_TRANSPORT", "stdio").lower()

    if transport == "http":
        host = os.environ.get("MCP_HOST", "0.0.0.0")
        port = int(os.environ.get("MCP_PORT", "8000"))
        raw_networks = os.environ.get("MCP_ALLOWED_NETWORKS", "0.0.0.0/0,::/0")
        allowed_networks = [n.strip() for n in raw_networks.split(",") if n.strip()]

        print(f"Starting cluefactory-ha-mcp over HTTP on {host}:{port}", file=sys.stderr)
        print(f"Allowed networks: {', '.join(allowed_networks)}", file=sys.stderr)

        # Disable FastMCP's localhost-only security — we handle access control
        # ourselves via IPAllowlistMiddleware above.
        mcp.settings.transport_security.enable_dns_rebinding_protection = False
        mcp.settings.transport_security.allowed_hosts = ["*"]
        mcp.settings.transport_security.allowed_origins = ["*"]
        mcp.settings.stateless_http = True

        app = mcp.streamable_http_app()
        app.add_middleware(IPAllowlistMiddleware, allowed_networks=allowed_networks)

        # Read version from the module docstring (line starting with "Version:")
        _version = "unknown"
        with open(__file__) as _f:
            for _line in _f:
                _m = re.match(r"^Version:\s+(.+)", _line.strip())
                if _m:
                    _version = _m.group(1)
                    break

        from starlette.routing import Route
        from starlette.responses import JSONResponse

        async def test_endpoint(request):
            return JSONResponse({"status": "success", "version": _version})

        app.routes.append(Route("/test", test_endpoint, methods=["GET"]))

        ssl_dir = os.environ.get("MCP_SSL_DIR_MOUNT", "").strip()
        ssl_kwargs: Dict[str, Any] = {}
        if ssl_dir:
            ssl_kwargs["ssl_certfile"] = os.path.join(ssl_dir, "cert.pem")
            ssl_kwargs["ssl_keyfile"] = os.path.join(ssl_dir, "privkey.pem")
            print(f"SSL enabled — certs from {ssl_dir}", file=sys.stderr)
        else:
            print("SSL disabled — running plain HTTP", file=sys.stderr)

        uvicorn.run(app, host=host, port=port, **ssl_kwargs)
    else:
        mcp.run(transport="stdio")
