"""MCP servers: extra tools a person adds in Settings, speaking the real MCP protocol over stdio.

Remote HTTP and SSE are not connected. A host named by the model, by a tool result, or by the server
itself would sit outside the fixed host list every other outbound call uses, so Jig does not open one.
"""

from .service import McpService

__all__ = ["McpService"]
