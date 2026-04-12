"""MCP Gateway — Doc §6.

The universal connector to any registered MCP server. In the Suadeo document
this is a trio of tools:
    mcp_list_tools(server)      — discover tools on a registered server
    mcp_call(server, tool, args) — execute a tool on any server
    mcp_register(name, url, auth) — add a new server at runtime

The Suadeo doc uses HTTP transport for MCP; we support both HTTP (via httpx)
and stdio (via the MCP Python client) selected per registry entry. The public
function signatures mirror Doc §6.4 exactly.

This module also holds the long-lived stdio client sessions so tools remain
callable throughout the life of the FastAPI app.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

logger = logging.getLogger(__name__)

REGISTRY_PATH = Path(__file__).parent.parent / "mcp_registry.json"


# -----------------------------------------------------------------------------
# Registry helpers
# -----------------------------------------------------------------------------

def _load_registry() -> dict:
    return json.loads(REGISTRY_PATH.read_text())


def _save_registry(registry: dict) -> None:
    REGISTRY_PATH.write_text(json.dumps(registry, indent=2))


# -----------------------------------------------------------------------------
# Tool description — what the gateway exposes to the LLM
# -----------------------------------------------------------------------------

@dataclass
class GatewayTool:
    """A tool discovered on a remote MCP server, namespaced as 'server.tool'."""
    server: str
    name: str                  # bare MCP tool name, e.g. 'get_schema'
    qualified_name: str        # 'suadeo.get_schema'
    description: str
    input_schema: dict


# -----------------------------------------------------------------------------
# Gateway — manages long-lived sessions to every registered MCP server
# -----------------------------------------------------------------------------

class MCPGateway:
    """Connects to every registered MCP server and caches their tools.

    Pattern adapted from:
    langgraph-mcp-agent/agent/mcp_tool_loader.py:MCPToolManager
    """

    def __init__(self, project_dir: Path):
        self.project_dir = Path(project_dir)
        # server_name -> (session, cm_stdio, cm_session) for cleanup
        self._sessions: dict[str, tuple] = {}
        # server_name -> list[GatewayTool]
        self._tools: dict[str, list[GatewayTool]] = {}

    # ------------- lifecycle -------------

    async def connect_all(self) -> dict[str, list[GatewayTool]]:
        registry = _load_registry()
        for name, cfg in registry["servers"].items():
            try:
                await self._connect_server(name, cfg)
            except Exception as e:
                logger.exception("Failed to connect to MCP server '%s': %s", name, e)
                self._tools[name] = []
        return self._tools

    async def _connect_server(self, name: str, cfg: dict) -> None:
        transport = cfg.get("transport", "stdio")
        if transport != "stdio":
            # HTTP transport — lazily called via httpx in mcp_call
            logger.info("[gateway] '%s' registered (http transport, lazy)", name)
            # For HTTP transport we discover tools eagerly via /tools/list
            tools = await self._http_list_tools(name, cfg)
            self._tools[name] = tools
            return

        # stdio transport — spawn the server as a subprocess
        args = cfg.get("args", [])
        # Resolve server script paths relative to project_dir
        resolved_args = []
        for a in args:
            if a.endswith(".py") and not os.path.isabs(a):
                resolved_args.append(str(self.project_dir / a))
            else:
                resolved_args.append(a)

        params = StdioServerParameters(
            command=cfg["command"],
            args=resolved_args,
            cwd=str(self.project_dir),
        )

        cm_stdio = stdio_client(params)
        read, write = await cm_stdio.__aenter__()
        cm_session = ClientSession(read, write)
        session = await cm_session.__aenter__()
        await session.initialize()

        # Discover tools
        tools_result = await session.list_tools()
        discovered: list[GatewayTool] = []
        for t in tools_result.tools:
            discovered.append(GatewayTool(
                server=name,
                name=t.name,
                qualified_name=f"{name}.{t.name}",
                description=t.description or "",
                input_schema=t.inputSchema or {},
            ))

        self._sessions[name] = (session, cm_stdio, cm_session)
        self._tools[name] = discovered
        logger.info("[gateway] '%s' connected (stdio) — %d tools", name, len(discovered))

    async def close_all(self) -> None:
        for name, (_, cm_stdio, cm_session) in self._sessions.items():
            try:
                await cm_session.__aexit__(None, None, None)
                await cm_stdio.__aexit__(None, None, None)
                logger.info("[gateway] '%s' disconnected", name)
            except Exception as e:
                logger.warning("[gateway] close '%s' failed: %s", name, e)
        self._sessions.clear()

    # ------------- inventory -------------

    def all_tools(self) -> list[GatewayTool]:
        result = []
        for tools in self._tools.values():
            result.extend(tools)
        return result

    def tools_for_server(self, server: str) -> list[GatewayTool]:
        return list(self._tools.get(server, []))

    def find(self, qualified_name: str) -> GatewayTool | None:
        for t in self.all_tools():
            if t.qualified_name == qualified_name:
                return t
        return None

    # ------------- Doc §6.4 public API -------------

    async def mcp_list_tools(self, server: str) -> str:
        """List available tools on a registered MCP server."""
        if server not in self._tools:
            # Try to load it if we know it
            registry = _load_registry()
            if server in registry["servers"]:
                await self._connect_server(server, registry["servers"][server])
            else:
                return json.dumps({"error": f"Unknown server '{server}'"})
        return json.dumps({
            "server": server,
            "tools": [
                {"name": t.name, "description": t.description, "input_schema": t.input_schema}
                for t in self._tools[server]
            ],
        })

    async def mcp_call(self, server: str, tool: str, args: dict[str, Any]) -> str:
        """Execute any tool on any registered MCP server."""
        registry = _load_registry()
        if server not in registry["servers"]:
            return json.dumps({"error": f"Unknown server '{server}'"})

        transport = registry["servers"][server].get("transport", "stdio")
        if transport == "stdio":
            if server not in self._sessions:
                await self._connect_server(server, registry["servers"][server])
            session, _, _ = self._sessions[server]
            try:
                result = await session.call_tool(tool, args)
                if result.content:
                    return result.content[0].text or ""
                return ""
            except Exception as e:
                return json.dumps({"error": str(e)})
        else:
            return await self._http_call(server, tool, args, registry["servers"][server])

    async def mcp_register(self, name: str, url: str, auth: str,
                           transport: str = "http") -> str:
        """Register a new MCP server at runtime (Doc §6.4)."""
        registry = _load_registry()
        registry["servers"][name] = {
            "transport": transport,
            "url": url,
            "auth": auth,
            "description": f"Registered at runtime",
        }
        _save_registry(registry)
        try:
            await self._connect_server(name, registry["servers"][name])
        except Exception as e:
            return json.dumps({"error": f"Registered but connect failed: {e}"})
        return json.dumps({"status": "ok", "server": name,
                           "tools_discovered": len(self._tools.get(name, []))})

    # ------------- HTTP transport helpers -------------

    async def _http_list_tools(self, name: str, cfg: dict) -> list[GatewayTool]:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(
                f"{cfg['url'].rstrip('/')}/tools/list",
                headers=self._auth_headers(cfg),
            )
            r.raise_for_status()
            payload = r.json()
        tools: list[GatewayTool] = []
        for t in payload.get("tools", []):
            tools.append(GatewayTool(
                server=name,
                name=t["name"],
                qualified_name=f"{name}.{t['name']}",
                description=t.get("description", ""),
                input_schema=t.get("inputSchema", {}),
            ))
        return tools

    async def _http_call(self, server: str, tool: str, args: dict, cfg: dict) -> str:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(
                f"{cfg['url'].rstrip('/')}/tools/call",
                headers=self._auth_headers(cfg),
                json={"name": tool, "arguments": args},
            )
            r.raise_for_status()
            return r.text

    @staticmethod
    def _auth_headers(cfg: dict) -> dict:
        auth = cfg.get("auth", "")
        if not auth:
            return {}
        # Expand env vars like "bearer ${TOKEN}"
        expanded = os.path.expandvars(auth)
        return {"Authorization": expanded}
