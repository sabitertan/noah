"""A tiny MCP server over stdio, started as a child process by tests/test_mcp.py (the transport most local servers use)."""
from mcp.server.mcpserver import MCPServer
from mcp_types import ToolAnnotations

srv = MCPServer("echo")


@srv.tool(description="Repeat a text back, in capitals.", annotations=ToolAnnotations(read_only_hint=True))
def shout(text: str) -> str:
    return text.upper()


if __name__ == "__main__":
    srv.run()   # stdio by default
