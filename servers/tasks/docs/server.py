"""Docs MCP server: keyword search over local documents, with BM25 on SQLite FTS5 (ADR-021).

The index lives at $ME3_DATA_DIR/docs_index.db. Documents stay where they are, under the
documents and workspace folders. No embeddings: only VAD, Smart Turn, and audio run locally.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated, Literal

from fastmcp import FastMCP
from pydantic import Field

from servers.common import OutsideRoots, ToolCallLog, data_dir, documents_dir, inside, serve, workspace_dir
from servers.documents import DOCUMENT_TYPES, extract_text

mcp = FastMCP("docs", instructions="Index local documents, then search them by keyword.")
mcp.add_middleware(ToolCallLog("docs"))

CHUNK_CHARS = 1600
OVERLAP_CHARS = 200
SNIPPET_CHARS = 500


def _roots() -> list[Path]:
    return [documents_dir(), workspace_dir()]


@contextmanager
def _db() -> Iterator[sqlite3.Connection]:
    db = sqlite3.connect(data_dir() / "docs_index.db")
    db.execute("CREATE TABLE IF NOT EXISTS files (path TEXT PRIMARY KEY, mtime REAL, size INTEGER)")
    db.execute("CREATE VIRTUAL TABLE IF NOT EXISTS chunks USING fts5(path UNINDEXED, text)")
    try:
        with db:
            yield db
    finally:
        db.close()


def chunks(text: str) -> Iterator[str]:
    step = CHUNK_CHARS - OVERLAP_CHARS
    for start in range(0, max(len(text) - OVERLAP_CHARS, 1), step):
        yield text[start : start + CHUNK_CHARS]


def _files(root: Path) -> Iterator[Path]:
    paths = [root] if root.is_file() else sorted(root.rglob("*"))
    for p in paths:
        hidden = any(part.startswith(".") for part in p.relative_to(root).parts) if p != root else False
        if p.is_file() and p.suffix.lower() in DOCUMENT_TYPES and not hidden:
            yield p


def _ingest(roots: list[Path], rebuild: bool = False) -> dict:
    indexed = skipped = 0
    failed: list[str] = []
    with _db() as db:
        if rebuild:
            db.execute("DELETE FROM files")
            db.execute("DELETE FROM chunks")
        known = {p: (m, s) for p, m, s in db.execute("SELECT path, mtime, size FROM files")}
        for root in roots:
            for path in _files(root):
                stat, key = path.stat(), str(path)
                if known.get(key) == (stat.st_mtime, stat.st_size):
                    skipped += 1
                    continue
                try:
                    text = extract_text(path)
                except Exception:  # noqa: BLE001, one bad file must not stop the rest
                    failed.append(path.name)
                    continue
                db.execute("DELETE FROM chunks WHERE path = ?", (key,))
                db.executemany("INSERT INTO chunks VALUES (?, ?)", ((key, c) for c in chunks(text)))
                db.execute("INSERT OR REPLACE INTO files VALUES (?, ?, ?)", (key, stat.st_mtime, stat.st_size))
                indexed += 1
        for key in known:
            if not Path(key).exists():
                db.execute("DELETE FROM chunks WHERE path = ?", (key,))
                db.execute("DELETE FROM files WHERE path = ?", (key,))
        total = db.execute("SELECT count(*) FROM files").fetchone()[0]
    return {"indexed": indexed, "unchanged": skipped, "failed": failed, "total_files": total}


@mcp.tool(
    annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}
)
def index_documents(
    path: Annotated[str | None, Field(description="A file or folder. Leave empty for all documents")] = None,
    rebuild: Annotated[bool, Field(description="Start the index again")] = False,
) -> dict:
    """Add documents to the local search index. Only new or changed files are read."""
    try:
        roots = [inside(path, _roots())] if path else _roots()
    except OutsideRoots as e:
        return {"error": str(e)}
    return _ingest(roots, rebuild)


@mcp.tool(
    annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}
)
def local_search(
    query: Annotated[str, Field(min_length=1, description="Words to look for")],
    top_k: Annotated[int, Field(ge=1, le=10)] = 5,
) -> dict:
    """Search the user's indexed documents by keyword, best match first."""
    words = re.findall(r"\w+", query)
    if not words:
        return {"results": []}
    with _db() as db:
        empty = not db.execute("SELECT 1 FROM files LIMIT 1").fetchone()
    if empty:
        _ingest(_roots())
    match = " OR ".join(f'"{w}"' for w in words)  # quoted, so words like NOT are not operators
    with _db() as db:
        rows = db.execute(
            "SELECT path, text, bm25(chunks) FROM chunks WHERE chunks MATCH ? ORDER BY bm25(chunks) LIMIT ?",
            (match, top_k),
        ).fetchall()
    return {"results": [{"path": Path(p).name, "text": t[:SNIPPET_CHARS], "score": round(-s, 3)}
                        for p, t, s in rows]}  # fmt: skip


@mcp.tool(
    annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}
)
def search_document(
    path: Annotated[str, Field(min_length=1, description="The document")],
    mode: Annotated[Literal["pattern", "context"], Field(description="pattern for a regex, context for keywords")] = "context",
    pattern: Annotated[str | None, Field(description="Regular expression, for pattern mode")] = None,
    query: Annotated[str | None, Field(description="Keywords, for context mode")] = None,
    max_matches: Annotated[int, Field(ge=1, le=50)] = 5,
) -> dict:
    """Search inside one document, by regular expression or by keywords."""
    try:
        target = inside(path, _roots())
    except OutsideRoots as e:
        return {"error": str(e)}
    if not target.is_file():
        return {"error": f"no document at {path}"}
    text = extract_text(target)
    if mode == "pattern":
        if not pattern:
            return {"error": "pattern mode needs a pattern"}
        try:
            rule = re.compile(pattern, re.I)
        except re.error as e:
            return {"error": f"bad pattern: {e}"}
        hits = [{"line": n, "text": line.strip()[:SNIPPET_CHARS]}
                for n, line in enumerate(text.splitlines(), 1) if rule.search(line)]  # fmt: skip
        return {"matches": hits[:max_matches], "total": len(hits)}
    words = {w.lower() for w in re.findall(r"\w+", query or "")}
    if not words:
        return {"error": "context mode needs a query"}
    scored = []
    for section in chunks(text):
        found = re.findall(r"\w+", section.lower())
        if score := sum(found.count(w) for w in words):
            scored.append((score, section))
    scored.sort(key=lambda s: -s[0])
    return {"matches": [{"text": s[:SNIPPET_CHARS], "score": n} for n, s in scored[:max_matches]],
            "total": len(scored)}  # fmt: skip


if __name__ == "__main__":
    serve(mcp)
