"""The knowledge, docs, and workspace servers (ADR-021). No network, no spend."""

from __future__ import annotations

import os
import time

import pytest

from harness.mcp_tools import _server_env
from servers.tasks.docs import server as docs
from servers.tasks.knowledge import server as knowledge
from servers.tasks.workspace import server as workspace


@pytest.fixture(autouse=True)
def data(tmp_path, monkeypatch):
    monkeypatch.setenv("ME3_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("ME3_WORKSPACE_LIVE", raising=False)
    monkeypatch.delenv("ME3_TOOL_STAGES", raising=False)
    return tmp_path


def test_every_backend_defaults_to_a_fake():
    assert knowledge.search("pi")["fake"]
    assert knowledge.fetch_content("https://example.com")["fake"]
    assert knowledge.get_weather()["fake"]


@pytest.mark.parametrize("stage,call", [
    ("search=ddgs", lambda: knowledge.search("pi")),
    ("search=tavily", lambda: knowledge.search("pi")),
    ("weather=open_meteo", lambda: knowledge.get_weather()),
])  # fmt: skip
def test_a_zero_cap_refuses_before_any_network(stage, call, monkeypatch):
    monkeypatch.setenv("ME3_TOOL_STAGES", stage)
    assert "cap is zero" in call()["error"]


@pytest.mark.parametrize("url", [
    "http://127.0.0.1:8000/", "http://localhost/", "http://169.254.169.254/latest/meta-data/",
    "http://10.0.0.1/", "http://[::1]/", "file:///etc/passwd", "ftp://example.com/",
])  # fmt: skip
def test_fetch_refuses_private_addresses(url):
    assert knowledge.public_url(url)


def test_fetch_refuses_a_redirect_to_a_private_address(monkeypatch):
    class Reply:
        is_redirect = True
        headers = {"location": "http://127.0.0.1/admin"}

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    checked = []
    real = knowledge.public_url
    monkeypatch.setattr(knowledge, "public_url", lambda u: checked.append(u) or (None if len(checked) == 1 else real(u)))
    monkeypatch.setattr(knowledge.httpx, "stream", lambda *a, **k: Reply())
    assert "private" in knowledge._get("https://example.com/")["error"]


def test_page_text_drops_scripts():
    html = b"<html><style>x</style><script>evil()</script><p>Hello</p><p>world</p></html>"
    assert knowledge.page_text(html, "text/html", "utf-8") == "Hello world"


def test_keys_reach_only_the_knowledge_server(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "x")
    monkeypatch.setenv("GROQ_API_KEY", "x")
    assert "TAVILY_API_KEY" not in _server_env(None, "servers.tasks.workspace.server")
    env = _server_env(None, "servers.tasks.knowledge.server")
    assert "TAVILY_API_KEY" in env and "GROQ_API_KEY" not in env


def test_spotify_credentials_still_reach_only_music(monkeypatch):
    monkeypatch.setenv("SPOTIFY_CLIENT_SECRET", "x")
    assert "SPOTIFY_CLIENT_SECRET" in _server_env(None, "servers.tasks.music.server")
    for module in ("knowledge", "docs", "workspace"):
        assert "SPOTIFY_CLIENT_SECRET" not in _server_env(None, f"servers.tasks.{module}.server")


def _doc(data, name, text):
    root = data / "documents"
    root.mkdir(exist_ok=True)
    (root / name).write_text(text)


def test_local_search_ranks_and_auto_indexes(data):
    _doc(data, "a.md", "Refunds refunds refunds on annual plans.")
    _doc(data, "b.md", "The office opens at nine, and refunds are rare.")
    results = docs.local_search("refunds")["results"]
    assert [r["path"] for r in results] == ["a.md", "b.md"]


def test_query_words_are_not_fts_operators(data):
    _doc(data, "a.md", "not a problem")
    assert docs.local_search('NOT "a" OR (problem')["results"]


def test_index_is_incremental_and_drops_deleted_files(data):
    _doc(data, "a.md", "alpha")
    assert docs.index_documents()["indexed"] == 1
    assert docs.index_documents()["indexed"] == 0
    (data / "documents" / "a.md").unlink()
    assert docs.index_documents()["total_files"] == 0
    assert docs.local_search("alpha")["results"] == []


def test_search_document_modes(data):
    _doc(data, "a.md", "one\nkey: 42\nthree")
    assert docs.search_document("a.md", mode="pattern", pattern=r"key: \d+")["matches"][0]["line"] == 2
    assert docs.search_document("a.md", query="key")["total"] == 1
    assert "bad pattern" in docs.search_document("a.md", mode="pattern", pattern="(")["error"]


def test_docx_and_xlsx_are_read(data):
    from docx import Document
    from openpyxl import Workbook

    root = data / "documents"
    root.mkdir()
    d = Document()
    d.add_paragraph("from word")
    d.save(root / "a.docx")
    book = Workbook()
    book.active.append(["from", "excel"])
    book.save(root / "b.xlsx")
    assert docs.local_search("word")["results"][0]["path"] == "a.docx"
    assert docs.local_search("excel")["results"][0]["path"] == "b.xlsx"


def test_files_round_trip():
    workspace.write_file("a/b.txt", "one two")
    assert workspace.read_file("a/b.txt")["text"] == "one two"
    assert workspace.edit_file("a/b.txt", "two", "three")["replaced"] == 1
    assert workspace.read_file("a/b.txt")["text"] == "one three"
    assert workspace.grep("three")["matches"][0]["file"] == "a/b.txt"


def test_edit_refuses_an_ambiguous_match():
    workspace.write_file("a.txt", "x x")
    assert "2 times" in workspace.edit_file("a.txt", "x", "y")["error"]


@pytest.mark.parametrize("path", ["../secret", "/etc/passwd", "a/../../secret"])
def test_paths_cannot_leave_the_workspace(path):
    assert "outside" in workspace.read_file(path)["error"]
    assert "outside" in workspace.write_file(path, "x")["error"]
    assert "outside" in workspace.edit_file(path, "a", "b")["error"]
    assert "outside" in workspace.send_file(path)["error"]
    assert "outside" in docs.search_document(path)["error"]
    assert "outside" in docs.index_documents(path)["error"]


def test_a_symlink_cannot_leave_the_workspace(data, tmp_path_factory):
    secret = tmp_path_factory.mktemp("outside") / "secret.txt"
    secret.write_text("key")
    workspace.write_file("x", "")
    (data / "workspace" / "link").symlink_to(secret)
    assert "outside" in workspace.read_file("link")["error"]
    assert "outside" in workspace.write_file("link", "x")["error"]
    assert secret.read_text() == "key"


def test_writes_cannot_reach_the_documents(data):
    _doc(data, "a.md", "text")
    assert workspace.read_file(str(data / "documents" / "a.md"))["text"] == "text"
    assert "outside" in workspace.write_file(str(data / "documents" / "a.md"), "x")["error"]


def test_bash_and_serve_are_mocks_unless_switched_on(data):
    marker = data / "workspace" / "ran"
    assert workspace.bash(f"touch {marker}")["ran"] is False
    assert workspace.serve("start", "web", "sleep 60")["ran"] is False
    assert not marker.exists()
    assert workspace.serve_status()["services"] == []


def test_send_file_goes_only_to_the_outbox(data):
    workspace.write_file("a.txt", "hi")
    result = workspace.send_file("a.txt")
    assert result["sent"] is False
    assert (data / "send_outbox" / result["saved_as"]).read_text() == "hi"


def test_live_bash_gets_no_secrets(monkeypatch):
    monkeypatch.setenv("ME3_WORKSPACE_LIVE", "1")
    monkeypatch.setenv("SPOTIFY_CLIENT_SECRET", "hidden")
    monkeypatch.setenv("SOME_TOKEN", "hidden")
    out = workspace.bash("env; pwd")
    assert out["returncode"] == 0
    assert "hidden" not in out["stdout"] and "workspace" in out["stdout"]


def test_live_bash_timeout_kills_the_command(monkeypatch, data):
    monkeypatch.setenv("ME3_WORKSPACE_LIVE", "1")
    monkeypatch.setattr(workspace, "BASH_TIMEOUT_S", 0.2)
    t0 = time.monotonic()
    assert "stopped after" in workspace.bash("sleep 30")["error"]
    assert time.monotonic() - t0 < 5


def test_live_serve_starts_logs_and_stops(monkeypatch):
    monkeypatch.setenv("ME3_WORKSPACE_LIVE", "1")
    pid = workspace.serve("start", "job", "echo up; sleep 30")["pid"]
    deadline = time.monotonic() + 5
    while "up" not in workspace.serve_status("job")["log"] and time.monotonic() < deadline:
        time.sleep(0.05)
    assert workspace.serve_status("job")["running"]
    assert "already running" in workspace.serve("start", "job", "sleep 1")["error"]
    workspace.serve("stop", "job")
    deadline = time.monotonic() + 5
    while workspace._alive(pid) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not workspace._alive(pid)
    assert workspace.serve_status()["services"] == []
    assert os.path.isdir(workspace.workspace_dir())
