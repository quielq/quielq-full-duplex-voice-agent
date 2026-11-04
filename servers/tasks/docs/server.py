"""Docs MCP server: keyword search over local documents, with BM25 on SQLite FTS5 (ADR-021).

The index lives at $ME3_DATA_DIR/docs_index.db. Documents stay where they are, under the
documents and workspace folders. No embeddings: only VAD, Smart Turn, and audio run locally.

A document agent (ADR-039) starts this server with ME3_DOC_SCOPE, a comma-separated list of
folders or files under the documents folder. Every tool then sees only those paths, so the
agent cannot read another agent's documents whatever the model asks for.
"""

from __future__ import annotations

import csv
import io
import os
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
READ_CHARS = 12000  # one read_document page: the customer list fits in one
_scope_indexed = False


class ScopeError(ValueError):
    """ME3_DOC_SCOPE names a path outside the documents folder."""


def scope() -> list[Path] | None:
    """The folders and files a document agent may see, or None for the generalist."""
    raw = os.environ.get("ME3_DOC_SCOPE", "").strip()
    if not raw:
        return None
    base = documents_dir().resolve()
    paths = []
    for item in filter(None, (i.strip() for i in raw.split(","))):
        target = (base / item).resolve()
        if not target.is_relative_to(base):
            raise ScopeError(f"{item} is outside the documents folder")
        paths.append(target)
    return paths


def _roots() -> list[Path]:
    return scope() or [documents_dir(), workspace_dir()]


def _in_scope_sql(roots: list[Path]) -> tuple[str, list]:
    """A WHERE clause that keeps only chunks under the roots. substr, not LIKE: folder names have _."""
    parts, args = [], []
    for r in roots:
        parts.append("(path = ? OR substr(path, 1, ?) = ?)")
        args += [str(r), len(str(r)) + 1, str(r) + os.sep]
    return " OR ".join(parts), args


def snippet(text: str, words: set[str]) -> str:
    """The lines of a chunk that match the query, best first, up to SNIPPET_CHARS. A chunk of CSV
    rows is about 1,600 characters, so its first 500 often miss the row that matched."""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    scored = []
    for i, line in enumerate(lines):
        found = re.findall(r"\w+", line.lower())
        if hits := sum(found.count(w) for w in words):
            scored.append((-hits, i, line))
    if not scored:
        return text[:SNIPPET_CHARS]
    picked, size = [], 0
    for _, i, line in sorted(scored):
        if picked and size + len(line) > SNIPPET_CHARS:
            break
        picked.append((i, line[:SNIPPET_CHARS]))
        size += len(line) + 1
    return "\n".join(line for _, line in sorted(picked))  # back in document order


def _shown(path: Path) -> str:
    """The name the model sees: relative to the documents folder when scoped, else the file name."""
    if scope() is not None:
        return str(path.relative_to(documents_dir().resolve()))
    return path.name


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


def csv_rows(text: str) -> list[str]:
    """One line per row, each with its column names, so a chunk still makes sense on its own."""
    reader = csv.reader(io.StringIO(text))
    header = next(reader, [])
    return ["; ".join(f"{h}: {v}" for h, v in zip(header, row) if v) for row in reader if any(row)]


def line_chunks(lines: list[str]) -> Iterator[str]:
    """Whole lines up to CHUNK_CHARS, so no row is cut in two."""
    chunk: list[str] = []
    size = 0
    for line in lines:
        if chunk and size + len(line) > CHUNK_CHARS:
            yield "\n".join(chunk)
            chunk, size = [], 0
        chunk.append(line)
        size += len(line) + 1
    if chunk:
        yield "\n".join(chunk)


def index_chunks(path: Path, text: str) -> Iterator[str]:
    if path.suffix.lower() == ".csv":
        return line_chunks(csv_rows(text))
    return chunks(text)


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
                db.executemany("INSERT INTO chunks VALUES (?, ?)", ((key, c) for c in index_chunks(path, text)))
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
    return _ingest(roots, rebuild and scope() is None)  # a scoped agent never wipes the shared index


def _ensure_indexed(roots: list[Path]) -> None:
    """Unscoped: index once when empty. Scoped: bring this agent's files up to date once per process."""
    global _scope_indexed
    if scope() is not None:
        if not _scope_indexed:
            _ingest(roots)
            _scope_indexed = True
        return
    with _db() as db:
        empty = not db.execute("SELECT 1 FROM files LIMIT 1").fetchone()
    if empty:
        _ingest(roots)


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
    roots = _roots()
    _ensure_indexed(roots)
    match = " OR ".join(f'"{w}"' for w in words)  # quoted, so words like NOT are not operators
    where, args = _in_scope_sql([r.resolve() for r in roots]) if scope() is not None else ("1", [])
    with _db() as db:
        rows = db.execute(
            f"SELECT path, text, bm25(chunks) FROM chunks WHERE chunks MATCH ? AND ({where}) "
            "ORDER BY bm25(chunks) LIMIT ?",
            (match, *args, top_k),
        ).fetchall()
    lowered = {w.lower() for w in words}
    return {"results": [{"path": _shown(Path(p)), "text": snippet(t, lowered), "score": round(-s, 3)}
                        for p, t, s in rows]}  # fmt: skip


@mcp.tool(
    annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}
)
def list_documents() -> dict:
    """List the documents you can search and read, with their sizes."""
    docs = []
    for root in _roots():
        for p in _files(root):
            docs.append({"path": _shown(p.resolve()), "kb": round(p.stat().st_size / 1024, 1)})
    return {"documents": docs[:200], "total": len(docs)}


@mcp.tool(
    annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}
)
def read_document(
    path: Annotated[str, Field(min_length=1, description="The document, as list_documents names it")],
    page: Annotated[int, Field(ge=1, description="Which page of text, for long documents")] = 1,
) -> dict:
    """Read a document's text a page at a time. Use it for short files or to count and compare rows."""
    try:
        target = _find(path)
    except OutsideRoots as e:
        return {"error": str(e)}
    if not target.is_file():
        return {"error": f"no document at {path}"}
    text = extract_text(target)
    if target.suffix.lower() == ".csv":
        text = "\n".join(csv_rows(text))
    pages = max(1, -(-len(text) // READ_CHARS))
    start = (page - 1) * READ_CHARS
    return {"path": _shown(target), "page": page, "pages": pages, "text": text[start : start + READ_CHARS]}


def _find(path: str) -> Path:
    """A path relative to the documents folder, or a bare file name found under the roots."""
    roots = _roots()
    scoped = scope() is not None
    target = inside(path, [documents_dir(), *roots] if scoped else roots)
    if not target.exists() and "/" not in path:
        for root in roots:
            for p in _files(root):
                if p.name == path:
                    return p.resolve()
    if scoped and not any(target == r or target.is_relative_to(r) for r in roots):
        raise OutsideRoots(f"{path} is outside this agent's documents")
    return target


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
        target = _find(path)
    except OutsideRoots as e:
        return {"error": str(e)}
    if not target.is_file():
        return {"error": f"no document at {path}"}
    text = extract_text(target)
    if target.suffix.lower() == ".csv":
        text = "\n".join(csv_rows(text))
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
    return {"matches": [{"text": snippet(s, words), "score": n} for n, s in scored[:max_matches]],
            "total": len(scored)}  # fmt: skip


if __name__ == "__main__":
    serve(mcp)
