"""Agent tools — Doc §4.

These are the 'external world + session context' tools that live inside the
Python Skill Router, as opposed to the Suadeo-platform tools exposed via the
MCP Server (servers/suadeo_sds_server.py).

Each tool is a plain Python async function that can be wrapped as a LangChain
StructuredTool by tools/registry.py.
"""
