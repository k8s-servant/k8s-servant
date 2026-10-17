"""Tools the model can call.

registry.py      the common Tool shape and the ToolRegistry
mcp_tools.py     tools published by kubernetes-mcp-server (preferred)
kubectl_tool.py  guarded read-only kubectl (fallback)

The load_skill tool lives next to the knowledge it serves, in knowledge.py.
"""
