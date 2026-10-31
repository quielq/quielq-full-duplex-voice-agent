"""Notes MCP server: short notes in SQLite at $ME3_DATA_DIR/notes.db."""

from __future__ import annotations

import sqlite3
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Annotated

from fastmcp import FastMCP
from pydantic import Field

from servers.common import ToolCallLog, data_dir, serve

mcp = FastMCP("notes", instructions="Short spoken notes. Notes are listed newest first.")
mcp.add_middleware(ToolCallLog("notes"))


@contextmanager
def _db() -> Iterator[sqlite3.Connection]:
    db = sqlite3.connect(data_dir() / "notes.db")
    db.row_factory = sqlite3.Row
    db.execute("CREATE TABLE IF NOT EXISTS notes (id TEXT PRIMARY KEY, text TEXT, created_at REAL)")
    try:
        with db:
            yield db
    finally:
        db.close()


@mcp.tool(
    annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False}
)
def add_note(text: Annotated[str, Field(min_length=1, description="The note to keep")]) -> dict:
    """Save a note."""
    note = {"id": uuid.uuid4().hex[:8], "text": text, "created_at": time.time()}
    with _db() as db:
        db.execute("INSERT INTO notes VALUES (:id, :text, :created_at)", note)
    return {"id": note["id"], "text": text}


@mcp.tool(
    annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}
)
def list_notes() -> dict:
    """List saved notes, newest first."""
    with _db() as db:
        rows = db.execute("SELECT id, text FROM notes ORDER BY created_at DESC").fetchall()
    return {"notes": [dict(r) for r in rows]}


@mcp.tool(
    annotations={"readOnlyHint": False, "destructiveHint": True, "idempotentHint": True, "openWorldHint": False}
)
def delete_note(note_id: str) -> dict:
    """Delete a note by id."""
    with _db() as db:
        deleted = db.execute("DELETE FROM notes WHERE id = ?", (note_id,)).rowcount
    if not deleted:
        return {"deleted": False, "reason": f"no note with id {note_id}"}
    return {"deleted": True, "id": note_id}


if __name__ == "__main__":
    serve(mcp)
