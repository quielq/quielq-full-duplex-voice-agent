"""The knowledge, docs, and workspace servers (ADR-021). No network, no spend."""

from __future__ import annotations

import io
import os
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from harness.mcp_tools import _server_env
from servers import common
from servers.tasks.docs import server as docs
from servers.tasks.knowledge import server as knowledge
from servers.tasks.workspace import server as workspace


@pytest.fixture(autouse=True)
def data(tmp_path, monkeypatch):
    monkeypatch.setenv("ME3_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("ME3_WORKSPACE_LIVE", raising=False)
    return tmp_path


def test_fakes_answer_under_the_test_stages():
    assert knowledge.search("pi")["fake"]
    assert knowledge.fetch_content("https://example.com")["fake"]
    assert knowledge.get_weather()["fake"]


def test_search_stays_fake_while_weather_and_fetch_are_real_by_default(monkeypatch):
    monkeypatch.delenv("ME3_TOOL_STAGES")
    assert [common.tool_stage(s) for s in ("search", "weather", "fetch")] == ["fake", "open_meteo", "http"]


class FakeStream:
    def __init__(self, address):
        self.address = address

    def get_extra_info(self, name):
        return (self.address, 443) if name == "server_addr" else None


@pytest.mark.parametrize("address,public", [("93.184.216.34", True), ("127.0.0.1", False), ("10.0.0.5", False)])
def test_the_connected_address_is_checked(address, public):
    assert knowledge.connected_to_public(FakeStream(address)) is public


def test_fetch_refuses_when_a_name_rebinds_to_a_private_address(monkeypatch):
    class Reply:
        extensions = {"network_stream": FakeStream("127.0.0.1")}

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(knowledge, "public_url", lambda u: None)
    monkeypatch.setattr(knowledge.httpx, "stream", lambda *a, **k: Reply())
    assert "private" in knowledge._get("https://example.com/")["error"]


def test_an_unreadable_page_is_an_answer_not_a_crash(monkeypatch):
    def broken(url):
        raise RuntimeError("bad pdf")

    monkeypatch.setenv("ME3_TOOL_STAGES", "fetch=http")
    monkeypatch.setattr(knowledge, "_get", broken)
    assert "fetch failed" in knowledge.fetch_content("https://example.com/x.pdf")["error"]


def small_pdf(pages):
    from pypdf import PdfWriter

    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(100, 100)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def test_a_pdf_page_past_the_end_is_empty_not_a_crash():
    assert knowledge.pdf_text(small_pdf(3), 9) == ("", 3)


def test_a_long_pdf_stops_reading_once_it_has_enough(monkeypatch):
    read = []

    class Page:
        def __init__(self, n):
            self.n = n

        def extract_text(self):
            read.append(self.n)
            return "x" * 3000

    import pypdf

    monkeypatch.setattr(pypdf, "PdfReader", lambda f: SimpleNamespace(pages=[Page(i) for i in range(100)]))
    text, pages = knowledge.pdf_text(b"", 11)
    assert pages == 100 and read == [10, 11] and len(text) > knowledge.MAX_CHARS


def test_every_weather_code_has_words():
    documented = [0, 1, 2, 3, 45, 48, 51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 71, 73, 75, 77, 80, 81, 82, 85, 86, 95, 96, 99]
    assert set(documented) <= set(knowledge.WEATHER_CODES)


@pytest.mark.parametrize("stage,call", [
    ("search=ddgs", lambda: knowledge.search("pi")),
    ("search=tavily", lambda: knowledge.search("pi")),
    ("weather=open_meteo", lambda: knowledge.get_weather()),
])  # fmt: skip
def test_a_zero_cap_refuses_before_any_network(stage, call, monkeypatch):
    config = common._providers()
    for provider in config["providers"].values():
        provider["daily_cap_usd"] = 0.0
    monkeypatch.setattr(common, "_providers", lambda: config)  # the real caps change with approvals
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
        extensions = {"network_stream": FakeStream("93.184.216.34")}

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


def test_list_files_shows_both_folders_newest_first(data):
    workspace.write_file("old.txt", "x")
    os.utime(data / "workspace" / "old.txt", (1_000_000, 1_000_000))
    workspace.write_file(".hidden", "x")
    (data / "documents").mkdir(exist_ok=True)
    (data / "documents" / "new.md").write_text("text")
    files = workspace.list_files()["files"]
    assert [Path(f["path"]).name for f in files] == ["new.md", "old.txt"]
    assert files[1]["modified"].startswith("1970")
    assert workspace.read_file(files[1]["path"])["text"] == "x"
    assert workspace.read_file(files[0]["path"])["text"] == "text"
    assert [f["path"] for f in workspace.list_files(file_pattern="*.txt")["files"]] == ["old.txt"]
    assert workspace.list_files(max_results=1)["truncated"] is True


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


def test_weather_retries_a_place_without_city(monkeypatch):
    asked = []

    def geocode(name):
        asked.append(name)
        return [] if name == "Taguig City" else [{"name": "City of Taguig", "latitude": 14.5, "longitude": 121.0}]

    now = {"temperature_2m": 26, "weather_code": 3, "wind_speed_10m": 2}
    answer = SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"current": now, "daily": {}})
    monkeypatch.setattr(knowledge, "_geocode", geocode)
    monkeypatch.setattr(knowledge.httpx, "get", lambda *a, **k: answer)
    out, calls = knowledge._open_meteo("Taguig City", False)
    assert asked == ["Taguig City", "Taguig"]
    assert out["place"] == "City of Taguig" and calls == 3


def test_an_unknown_place_stays_an_error(monkeypatch):
    monkeypatch.setattr(knowledge, "_geocode", lambda name: [])
    assert knowledge._open_meteo("Nowhere City", False) == ({"error": "could not find Nowhere City"}, 2)


def test_a_write_reports_where_the_file_went(data):
    out = workspace.write_file("a.txt", "x")
    assert out["full_path"] == str(common.workspace_dir() / "a.txt")
    listed = workspace.list_files()
    assert listed["workspace_folder"] == str(common.workspace_dir())
    assert listed["documents_folder"] == str(common.documents_dir())


def test_the_mock_player_says_it_is_a_mock(data):
    from servers.tasks.music.backends import MockBackend

    assert MockBackend().play("Dive")["mock"] is True


def test_fetch_retries_a_403_once(monkeypatch):
    answers = [{"error": knowledge.BLOCKED}, {"text": "page"}]
    monkeypatch.setenv("ME3_TOOL_STAGES", "fetch=http")
    monkeypatch.setattr(knowledge, "FETCH_RETRY_S", 0)
    monkeypatch.setattr(knowledge, "_get", lambda url, page: answers.pop(0))
    assert knowledge.fetch_content("https://example.com/")["text"] == "page"
