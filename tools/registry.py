"""Tool Registry — glue between SKILL.md tool names and LangChain tools.

Produces a dict { qualified_name -> StructuredTool } where:
  - MCP server tools are namespaced:  'suadeo.get_schema', 'gitlab.get_commits'
  - Agent tools use their bare name:  'web_search', 'analyze_code_quality'
  - Gateway tools are exposed as:     'mcp_list_tools', 'mcp_call', 'mcp_register'

SKILL.md files reference tools using exactly these qualified names. The
executor filters `all_tools` down to `skill.tools` before binding them to the
LLM, which implements the per-skill allowlist the Suadeo doc prescribes.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from typing import Any, Callable

from langchain_core.tools import StructuredTool
from pydantic import create_model

from .mcp_gateway import GatewayTool, MCPGateway
from . import web_search as t_web_search
from . import web_fetch as t_web_fetch
from . import read_document as t_read_document
from . import get_user_context as t_get_user_context
from . import analyze_code_quality as t_analyze_code_quality
from . import generate_chart as t_generate_chart

logger = logging.getLogger(__name__)


# -----------------------------------------------------------------------------
# Local agent tool definitions
# -----------------------------------------------------------------------------

_JSON_TYPE_MAP = {"string": str, "integer": int, "number": float,
                  "boolean": bool, "array": list, "object": dict}


def _local_agent_tools() -> list[StructuredTool]:
    """Wrap the 6 agent tool functions from Doc §4.2 as LangChain tools."""
    defs: list[tuple[str, str, Callable]] = [
        ("web_search",
         "Real-time web search via DuckDuckGo. Returns top results with title, URL, snippet.",
         t_web_search.web_search),
        ("web_fetch",
         "Fetch the full text content of a URL. Use after web_search to read a full article.",
         t_web_fetch.web_fetch),
        ("read_document",
         "Read an uploaded file (PDF, Word, Excel) and return extracted text + metadata.",
         t_read_document.read_document),
        ("get_user_context",
         "Return the current user's profile, roles and dataset-level access rights.",
         t_get_user_context.get_user_context),
        ("analyze_code_quality",
         "Analyse a GitLab diff and return a structured quality report.",
         t_analyze_code_quality.analyze_code_quality),
        ("generate_chart",
         "Generate Plotly JSON for tabular data (bar, line, scatter, pie).",
         t_generate_chart.generate_chart),
    ]

    tools: list[StructuredTool] = []
    for name, desc, func in defs:
        tools.append(_function_to_structured_tool(name, desc, func))
    return tools


def _function_to_structured_tool(name: str, description: str,
                                 func: Callable) -> StructuredTool:
    """Inspect a Python function signature and build a StructuredTool."""
    sig = inspect.signature(func)
    fields: dict[str, tuple[type, Any]] = {}
    for pname, param in sig.parameters.items():
        annot = param.annotation if param.annotation is not inspect.Parameter.empty else str
        # Convert generic aliases like list[Any] to their origin
        origin = getattr(annot, "__origin__", None)
        if origin is list:
            annot = list
        elif origin is dict:
            annot = dict
        default = ... if param.default is inspect.Parameter.empty else param.default
        fields[pname] = (annot, default)
    ArgsModel = create_model(f"{name}_args", **fields) if fields else create_model(f"{name}_args")

    is_coro = inspect.iscoroutinefunction(func)

    async def acall(**kwargs):
        result = func(**kwargs)
        if inspect.iscoroutine(result):
            result = await result
        return result

    def scall(**kwargs):
        if is_coro:
            return asyncio.run(func(**kwargs))
        return func(**kwargs)

    return StructuredTool(
        name=name,
        description=description,
        args_schema=ArgsModel,
        func=scall,
        coroutine=acall,
    )


# -----------------------------------------------------------------------------
# Gateway tool wrapping (Doc §6.4)
# -----------------------------------------------------------------------------

def _gateway_meta_tools(gateway: MCPGateway) -> list[StructuredTool]:
    """The 3 gateway meta-tools from Doc §6.4: mcp_list_tools, mcp_call, mcp_register."""

    async def _list(server: str) -> str:
        return await gateway.mcp_list_tools(server)

    async def _call(server: str, tool: str, args: dict | None = None) -> str:
        return await gateway.mcp_call(server, tool, args or {})

    async def _register(name: str, url: str, auth: str = "") -> str:
        return await gateway.mcp_register(name, url, auth)

    return [
        _function_to_structured_tool(
            "mcp_list_tools",
            "Discover the tools available on a registered MCP server. Parameter: server (registry name).",
            _list,
        ),
        _function_to_structured_tool(
            "mcp_call",
            "Execute any tool on any registered MCP server. Parameters: server, tool, args.",
            _call,
        ),
        _function_to_structured_tool(
            "mcp_register",
            "Register a new MCP server at runtime. Parameters: name, url, auth.",
            _register,
        ),
    ]


# -----------------------------------------------------------------------------
# MCP server tool wrapping — build namespaced StructuredTools from GatewayTool
# -----------------------------------------------------------------------------

def _mcp_tool_to_structured(gateway: MCPGateway, gt: GatewayTool) -> StructuredTool:
    """Wrap a remote MCP tool as a LangChain StructuredTool.

    The tool's .name is the qualified 'server.tool' form (with '_' because
    LangChain / Gemini sometimes reject '.'). We keep a mapping for display.
    """
    # Gemini rejects '.' in function names → use '__'
    lc_name = f"{gt.server}__{gt.name}"

    # Build Pydantic model from input schema
    fields: dict[str, tuple[type, Any]] = {}
    props = (gt.input_schema or {}).get("properties", {})
    required = set((gt.input_schema or {}).get("required", []))
    for pname, pinfo in props.items():
        ptype = _JSON_TYPE_MAP.get(pinfo.get("type", "string"), str)
        default = ... if pname in required else pinfo.get("default", None)
        fields[pname] = (ptype, default)
    ArgsModel = create_model(f"{lc_name}_args", **fields) if fields else create_model(f"{lc_name}_args")

    server = gt.server
    tool_name = gt.name

    async def acall(**kwargs):
        return await gateway.mcp_call(server, tool_name, kwargs)

    def scall(**kwargs):
        return asyncio.run(gateway.mcp_call(server, tool_name, kwargs))

    return StructuredTool(
        name=lc_name,
        description=gt.description or f"{server}.{tool_name}",
        args_schema=ArgsModel,
        func=scall,
        coroutine=acall,
    )


# -----------------------------------------------------------------------------
# Public builder
# -----------------------------------------------------------------------------

class ToolRegistry:
    """Holds every tool known to the Skill Router, indexed by qualified name.

    Qualified naming convention (matches SKILL.md files):
      - MCP server tool:  'suadeo.get_schema'
      - Agent tool:       'web_search'
      - Gateway:          'mcp_call'
    """

    def __init__(self, gateway: MCPGateway):
        self.gateway = gateway
        self.tools: dict[str, StructuredTool] = {}
        # Map LangChain-safe names back to qualified names for audit output
        self.lc_to_qualified: dict[str, str] = {}
        self.qualified_to_lc: dict[str, str] = {}

    def build(self) -> None:
        # 1. Agent tools (bare names)
        for t in _local_agent_tools():
            self.tools[t.name] = t
            self.qualified_to_lc[t.name] = t.name
            self.lc_to_qualified[t.name] = t.name

        # 2. Gateway meta tools
        for t in _gateway_meta_tools(self.gateway):
            self.tools[t.name] = t
            self.qualified_to_lc[t.name] = t.name
            self.lc_to_qualified[t.name] = t.name

        # 3. MCP server tools — namespaced
        for gt in self.gateway.all_tools():
            lc_tool = _mcp_tool_to_structured(self.gateway, gt)
            qualified = gt.qualified_name  # 'suadeo.get_schema'
            self.tools[qualified] = lc_tool
            self.qualified_to_lc[qualified] = lc_tool.name       # 'suadeo__get_schema'
            self.lc_to_qualified[lc_tool.name] = qualified

        logger.info("[registry] %d tools registered", len(self.tools))

    def select(self, allowed_qualified_names: list[str]) -> list[StructuredTool]:
        """Return the subset of tools whose qualified name is in the allowlist.

        This enforces the per-skill tool filtering prescribed by the Suadeo doc.
        """
        selected = []
        for name in allowed_qualified_names:
            tool = self.tools.get(name)
            if tool is None:
                logger.warning("[registry] skill references unknown tool: %s", name)
                continue
            selected.append(tool)
        return selected

    def list_qualified_names(self) -> list[str]:
        return sorted(self.tools.keys())

    def lookup_qualified(self, lc_name: str) -> str:
        """Given a LangChain-safe tool name, return the original qualified name."""
        return self.lc_to_qualified.get(lc_name, lc_name)
