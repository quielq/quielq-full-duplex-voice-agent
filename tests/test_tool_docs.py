"""docs/tools.md lists every tool, so a new one cannot ship undocumented."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_every_tool_is_documented():
    docs = (ROOT / "docs" / "tools.md").read_text()
    tools = [name for path in (ROOT / "servers" / "tasks").glob("*/server.py")
             for name in re.findall(r"@mcp\.tool\([^@]*?\)\n(?:async )?def (\w+)", path.read_text())]  # fmt: skip
    assert len(tools) > 30
    assert [t for t in tools if f"`{t}`" not in docs] == []
