"""Stdio smoke test: launch the installed wdmcp server and drive a few tools over MCP."""
import asyncio
import sys
from pathlib import Path

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

EXE = Path(__file__).resolve().parents[1] / ".venv" / "Scripts" / "wdmcp.exe"


def text_of(result) -> str:
    return " | ".join(c.text for c in result.content if c.type == "text")


async def main() -> int:
    params = StdioServerParameters(command=str(EXE), args=[])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            names = sorted(t.name for t in tools.tools)
            print(f"{len(names)} tools: {', '.join(names)}")

            for name, args in (
                ("new_map", {"width": 640, "height": 400, "theme": "Adventure"}),
                ("generate_landmass", {"coverage": 0.4, "seed": 7, "kind": "continent"}),
            ):
                r = await session.call_tool(name, args)
                print(f"{name}: error={r.is_error} {text_of(r)[:160]}")
                if r.is_error:
                    return 1

            r = await session.call_tool("render_preview", {"scale": 0.5})
            if r.is_error:
                print("render_preview error:", text_of(r))
                return 1
            images = [c for c in r.content if c.type == "image"]
            for c in images:
                print(f"render_preview image: type={c.type} mime={c.mime_type} "
                      f"base64_len={len(c.data)}")
            print("render_preview text:", text_of(r))
            r = await session.call_tool("add_label", {"text": "x", "pos": [10, 10]})
            print("add_label ok:", not r.is_error)
            r = await session.call_tool("undo", {})
            print("undo ok:", not r.is_error)
            bad = await session.call_tool("add_symbol", {"set_key": "res://nope", "pos": [1, 1]})
            print("expected tool error:", bad.is_error, "-", text_of(bad)[:120])
            return 0 if images else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
