"""Exercise the installed plugin over MCP; print counts only, never ticket content."""

import asyncio
import json
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from dynamix_manager.plugin import APP_URI


async def check():
    cache = Path.home() / ".codex/plugins/cache/personal/teamdynamix"
    root = max((p for p in cache.iterdir() if p.is_dir()), key=lambda p: p.stat().st_mtime)
    config = json.loads((root / ".mcp.json").read_text())["mcpServers"]["teamdynamix"]
    async with stdio_client(StdioServerParameters(**config)) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = (await session.list_tools()).tools
            print(f"Installed MCP tools: {len(tools)}")
            async def invoke(name, args=None):
                result = await session.call_tool(name, args or {})
                if result.isError:
                    raise RuntimeError(f"Live MCP check failed: {name}")
                if not isinstance(result.structuredContent, dict):
                    raise RuntimeError(f"Missing structured output: {name}")
                print(f"PASS {name}")
                return result.structuredContent
            status = await invoke("connection_status")
            assert status["ticket_app_id"] == 634
            await invoke("ticket_statuses")
            rows = await invoke("search_tickets", {"limit": 1})
            if rows["tickets"]:
                ticket_id = rows["tickets"][0]["ID"]
                await invoke("get_ticket", {"ticket_id": ticket_id})
                await invoke("ticket_feed", {"ticket_id": ticket_id, "limit": 1})
            if status["authentication"] == "user":
                await invoke("my_queue", {"limit": 1})
            await invoke("survey_report", {"limit": 1})
            await invoke("days_off")
            resource = await session.read_resource(APP_URI)
            assert resource.contents[0].mimeType == "text/html;profile=mcp-app"
            print("PASS embedded app resource")


if __name__ == "__main__":
    asyncio.run(check())
