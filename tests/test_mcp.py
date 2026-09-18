import importlib.util
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest


@unittest.skipUnless(importlib.util.find_spec("mcp"), "Install the mcp extra to test the stdio adapter")
class McpTests(unittest.IsolatedAsyncioTestCase):
    async def test_stdio_handshake_and_tools_without_api_key(self):
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
        with TemporaryDirectory() as tmp:
            params = StdioServerParameters(command=sys.executable, args=[
                "-m", "krilin", "--bridge-config", str(Path(tmp) / "missing.json"),
                "serve", "--env-file", str(Path(tmp) / "missing.env"),
            ])
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    tools = (await session.list_tools()).tools
                    self.assertEqual({t.name for t in tools}, {"android_observe", "android_run"})
                    run = next(t for t in tools if t.name == "android_run")
                    self.assertIn("assertions", run.inputSchema["required"])
                    result = await session.call_tool("android_observe", {})
                    self.assertTrue(result.isError)
                    self.assertIn("krilin setup", str(result.content))
