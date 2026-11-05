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


def test_list_files_sums_up_every_top_level_folder(data):
    # On Nov 4 Haiku named only the newest folder when asked to list the workspace.
    for i in range(25):
        workspace.write_file(f"site/page{i}.html", "x")
    workspace.write_file("notes/a.txt", "x")
    workspace.write_file("packing.txt", "x")
    out = workspace.list_files(max_results=5)
    assert out["truncated"] is True
    assert out["workspace_summary"] == {"folders": {"notes": 1, "site": 25}, "files": ["packing.txt"],
                                        "total_files": 27}  # fmt: skip


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


def test_open_in_browser_is_a_mock_until_switched_on(data, monkeypatch):
    calls = []
    monkeypatch.setattr(workspace.subprocess, "run", lambda *a, **k: calls.append(a))
    workspace.write_file("a.html", "<h1>hi</h1>")
    assert workspace.open_in_browser("a.html")["ran"] is False
    assert calls == []


def test_open_in_browser_gets_one_file_and_no_secrets(data, monkeypatch):
    monkeypatch.setenv("ME3_WORKSPACE_LIVE", "1")
    monkeypatch.setenv("SPOTIFY_CLIENT_SECRET", "hidden")
    seen = {}

    def run(argv, env, **kw):
        seen.update(argv=argv, env=env, shell=kw.get("shell", False))

    monkeypatch.setattr(workspace.subprocess, "run", run)
    workspace.write_file("a b.HTML", "<h1>hi</h1>")
    assert workspace.open_in_browser("a b.HTML") == {"opened": "a b.HTML"}
    assert Path(seen["argv"][0]).name in ("open", "xdg-open") and len(seen["argv"]) == 2
    assert seen["argv"][1].endswith("/workspace/a b.HTML")
    assert "hidden" not in seen["env"].values() and seen["shell"] is False


@pytest.mark.parametrize(("path", "error"), [
    ("note.txt", "only HTML"), ("run.command", "only HTML"), ("missing.html", "no file"), ("../secret.html", "outside"),
])  # fmt: skip
def test_open_in_browser_refuses_all_but_workspace_html(data, monkeypatch, path, error):
    monkeypatch.setenv("ME3_WORKSPACE_LIVE", "1")
    monkeypatch.setattr(workspace.subprocess, "run", lambda *a, **k: pytest.fail("the opener ran"))
    workspace.write_file("note.txt", "hi")
    workspace.write_file("run.command", "hi")
    assert error in workspace.open_in_browser(path)["error"]


def test_open_in_browser_refuses_a_symlink_out_of_the_workspace(data, tmp_path_factory, monkeypatch):
    monkeypatch.setenv("ME3_WORKSPACE_LIVE", "1")
    outside = tmp_path_factory.mktemp("outside") / "page.html"
    outside.write_text("hi")
    (common.workspace_dir() / "link.html").symlink_to(outside)
    assert "outside" in workspace.open_in_browser("link.html")["error"]


def test_a_failed_opener_is_an_error(data, monkeypatch):
    monkeypatch.setenv("ME3_WORKSPACE_LIVE", "1")

    def fail(argv, **kw):
        raise workspace.subprocess.CalledProcessError(1, argv)

    monkeypatch.setattr(workspace.subprocess, "run", fail)
    workspace.write_file("a.html", "hi")
    assert "could not be opened" in workspace.open_in_browser("a.html")["error"]


def test_live_bash_gets_no_secrets(monkeypatch):
    monkeypatch.setenv("ME3_WORKSPACE_LIVE", "1")
    monkeypatch.setenv("SPOTIFY_CLIENT_SECRET", "hidden")
    monkeypatch.setenv("SOME_TOKEN", "hidden")
    out = workspace.bash("python3 -c 'import os; print(dict(os.environ))' && pwd")  # env itself is refused
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


def test_a_rewrite_keeps_the_old_version(tmp_path, monkeypatch):
    monkeypatch.setenv("ME3_DATA_DIR", str(tmp_path))
    from servers.tasks.workspace import server as workspace

    assert "previous_version" not in workspace.write_file("a.txt", "one")
    kept = workspace.write_file("a.txt", "two")["previous_version"]
    assert (tmp_path / "workspace" / kept).read_text() == "one"
    assert workspace.edit_file("a.txt", "two", "three")["previous_version"].startswith(".versions/")
    assert [f["path"] for f in workspace.list_files()["files"]] == ["a.txt"]


@pytest.fixture
def scoped(data, monkeypatch):
    """Two agents' folders, with this server scoped to the first (ADR-039)."""
    root = data / "documents"
    (root / "it_policy").mkdir(parents=True)
    (root / "workouts").mkdir()
    (root / "it_policy" / "security.md").write_text("Passwords are at least 14 characters long.")
    (root / "workouts" / "plan.md").write_text("Squat passwords at 100 kg for five sets.")
    (root / "it_policy_old.md").write_text("Passwords are 8 characters.")  # a sibling that shares the prefix
    monkeypatch.setenv("ME3_DOC_SCOPE", "it_policy")
    monkeypatch.setattr(docs, "_scope_indexed", False)
    return root


def test_scoped_search_sees_only_its_folder(scoped):
    results = docs.local_search("passwords")["results"]
    assert [r["path"] for r in results] == ["it_policy/security.md"]


def test_scoped_search_skips_files_another_agent_indexed(scoped, monkeypatch):
    monkeypatch.delenv("ME3_DOC_SCOPE")
    assert len(docs.index_documents()["failed"]) == 0  # the generalist indexes everything
    monkeypatch.setenv("ME3_DOC_SCOPE", "it_policy")
    assert {r["path"] for r in docs.local_search("passwords")["results"]} == {"it_policy/security.md"}


def test_scoped_tools_refuse_other_folders(scoped):
    assert "outside" in docs.read_document("workouts/plan.md")["error"]
    assert "outside" in docs.search_document("../workouts/plan.md", query="squat")["error"]
    assert "outside" in docs.read_document("it_policy_old.md")["error"]
    assert [d["path"] for d in docs.list_documents()["documents"]] == ["it_policy/security.md"]


def test_scoped_read_finds_a_bare_file_name(scoped):
    assert "14 characters" in docs.read_document("security.md")["text"]


def test_scope_outside_documents_is_refused(data, monkeypatch):
    monkeypatch.setenv("ME3_DOC_SCOPE", "../workspace")
    with pytest.raises(docs.ScopeError):
        docs.scope()


def test_csv_rows_keep_their_column_names(data):
    _doc(data, "c.csv", "customer_id,company,status\nC-1,Mayon Builders,Active\nC-2,Taal Foods,Churned\n")
    hit = docs.local_search("Taal")["results"][0]["text"]
    assert hit == "customer_id: C-2; company: Taal Foods; status: Churned"
    assert docs.read_document("c.csv")["text"].startswith("customer_id: C-1")
    assert docs.search_document("c.csv", mode="pattern", pattern="Churned")["matches"][0]["line"] == 2


def test_read_document_pages_long_text(data):
    _doc(data, "long.md", "x" * (docs.READ_CHARS + 10))
    first = docs.read_document("long.md")
    assert first["pages"] == 2 and len(docs.read_document("long.md", page=2)["text"]) == 10


def test_a_match_deep_in_a_chunk_reaches_the_snippet(data):
    rows = "\n".join(f"C-{i},Filler Company Number {i},Retail,Somewhere Long City Name" for i in range(30))
    _doc(data, "c.csv", "customer_id,company,industry,city\n" + rows + "\nC-99,Mayon Builders,Construction,Davao\n")
    assert "Mayon Builders" in docs.local_search("Mayon Builders")["results"][0]["text"]
    assert "Mayon Builders" in docs.search_document("c.csv", query="Mayon")["matches"][0]["text"]


def test_prose_snippets_still_start_at_the_chunk_when_nothing_matches_a_line():
    assert docs.snippet("x" * 900, {"absent"}) == "x" * docs.SNIPPET_CHARS


def test_a_table_shows_its_shape_and_both_ends(data):
    _doc(data, "log.csv", "title,start_time\nLight,\"Oct 29, 2026\"\nHeavy,\"Oct 27, 2026\"\nFirst,\"Jan 2, 2025\"\n")
    _doc(data, "notes.md", "plain text")
    listed = {d["path"]: d for d in docs.list_documents()["documents"]}
    table = listed["log.csv"]
    assert table["rows"] == 3 and table["columns"] == ["title", "start_time"] and table["pages"] == 1
    assert table["first_row"] == "title: Light; start_time: Oct 29, 2026"
    assert table["last_row"].startswith("title: First")
    assert "rows" not in listed["notes.md"]
