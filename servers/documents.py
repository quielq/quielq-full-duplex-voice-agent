"""Text from the document types the tools read: plain text, PDF, DOCX, and XLSX."""

from __future__ import annotations

from pathlib import Path

TEXT_TYPES = {".txt", ".md", ".csv", ".log", ".json", ".yaml", ".yml", ".py", ".html"}
DOCUMENT_TYPES = TEXT_TYPES | {".pdf", ".docx", ".xlsx"}


def extract_text(path: Path) -> str:
    kind = path.suffix.lower()
    if kind == ".pdf":
        from pypdf import PdfReader

        return "\n".join(page.extract_text() or "" for page in PdfReader(path).pages)
    if kind == ".docx":
        from docx import Document

        return "\n".join(p.text for p in Document(str(path)).paragraphs)
    if kind == ".xlsx":
        from openpyxl import load_workbook

        book = load_workbook(path, read_only=True, data_only=True)
        rows = (row for sheet in book for row in sheet.iter_rows(values_only=True))
        return "\n".join(", ".join("" if c is None else str(c) for c in row) for row in rows)
    return path.read_text(errors="replace")
