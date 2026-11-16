"""publish_site (ADR-049): one workspace folder to one GitHub Pages repo. A fake GitHub, no network."""

from __future__ import annotations

import base64
import json

import httpx
import pytest
import yaml

from harness.mcp_tools import _server_env
from harness.tool_policy import readback_text
from servers import common
from servers.tasks.workspace import server as workspace

TOKEN = "github_pat_test_only_not_real"
REPO = "quielq/me3-sites"
POLICY = yaml.safe_load((common.REPO_ROOT / "configs" / "tool_policy.yaml").read_text())


class FakeGitHub:
    """The Git Data API calls _publish makes, on one repo with one branch."""

    def __init__(self, files: dict[str, str] | None = None, status: dict[str, int] | None = None):
        self.files = dict(files or {"README.md": "hi"})
        self.status = status or {}
        self.requests: list[httpx.Request] = []
        self.blobs: dict[str, bytes] = {}
        self.tree_body: dict = {}
        self.ref_body: dict = {}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path.removeprefix(f"/repos/{REPO}")
        for prefix, code in self.status.items():
            if path.startswith(prefix):
                return httpx.Response(code, json={"message": "nope"})
        body = json.loads(request.content) if request.content else {}
        if path == "":
            return httpx.Response(200, json={"default_branch": "main"})
        if path == "/git/ref/heads/main":
            return httpx.Response(200, json={"object": {"sha": "head0"}})
        if path == "/git/commits/head0":
            return httpx.Response(200, json={"tree": {"sha": "tree0"}})
        if path == "/git/trees/tree0":
            return httpx.Response(200, json={"truncated": False, "tree": [
                {"path": p, "type": "blob"} for p in self.files]})  # fmt: skip
        if path == "/git/blobs":
            sha = f"blob{len(self.blobs)}"
            self.blobs[sha] = base64.b64decode(body["content"])
            return httpx.Response(201, json={"sha": sha})
        if path == "/git/trees":
            self.tree_body = body
            return httpx.Response(201, json={"sha": "tree1"})
        if path == "/git/commits":
            return httpx.Response(201, json={"sha": "commit1abcdef"})
        if path == "/git/refs/heads/main":
            self.ref_body = body
            return httpx.Response(200, json={})
        return httpx.Response(404, json={})


@pytest.fixture(autouse=True)
def data(tmp_path, monkeypatch):
    monkeypatch.setenv("ME3_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("ME3_PAGES_TOKEN", raising=False)
    monkeypatch.delenv("ME3_PAGES_REPO", raising=False)
    return tmp_path


@pytest.fixture
def live(monkeypatch):
    """Switched on, with a fake GitHub behind the real client."""
    monkeypatch.setenv("ME3_TOOL_STAGES", common.FAKE_TOOL_STAGES + ",pages=github")
    monkeypatch.setenv("ME3_PAGES_TOKEN", TOKEN)
    monkeypatch.setenv("ME3_PAGES_REPO", REPO)
    fake = FakeGitHub()
    real = workspace._GitHub
    monkeypatch.setattr(workspace, "_GitHub", lambda repo, token: real(repo, token, transport=httpx.MockTransport(fake)))
    return fake


def _site(name: str = "site") -> None:
    workspace.write_file(f"{name}/index.html", "<h1>hi</h1>")
    workspace.write_file(f"{name}/css/style.css", "h1{color:red}")
    workspace.write_file(f"{name}/plan.md", "the plan")
    workspace.write_file(f"{name}/.secret.html", "hidden")


def test_it_is_a_mock_until_switched_on(monkeypatch):
    _site()
    monkeypatch.setattr(workspace, "_GitHub", lambda *a, **k: pytest.fail("GitHub was called"))
    result = workspace.publish_site("site", "demo")
    assert result["mock"] is True and result["published"] is False
    assert result["files"] == 2 and result["skipped"] == 2


def test_it_publishes_only_web_files_into_the_site_folder(live):
    _site()
    result = workspace.publish_site("site", "demo")
    assert result == {"published": True, "url": "https://quielq.github.io/me3-sites/demo/", "files": 2,
                      "skipped": 2, "commit": "commit1", "note": "GitHub Pages takes about a minute to show it."}  # fmt: skip
    paths = {e["path"] for e in live.tree_body["tree"]}
    assert paths == {"demo/index.html", "demo/css/style.css", ".nojekyll"}
    assert live.tree_body["base_tree"] == "tree0"
    assert sorted(live.blobs.values()) == [b"<h1>hi</h1>", b"h1{color:red}"]
    assert live.ref_body == {"sha": "commit1abcdef", "force": False}


def test_a_republish_removes_old_files_of_that_site_only(live):
    live.files = {"README.md": "", ".nojekyll": "", "demo/old.html": "", "other/index.html": ""}
    _site()
    workspace.publish_site("site", "demo")
    gone = {e["path"] for e in live.tree_body["tree"] if e.get("sha", "") is None}
    assert gone == {"demo/old.html"}
    assert ".nojekyll" not in {e["path"] for e in live.tree_body["tree"]}


def test_the_token_goes_only_in_the_header_to_github(live):
    _site()
    workspace.publish_site("site", "demo")
    assert {r.url.host for r in live.requests} == {"api.github.com"}
    assert all(r.headers["Authorization"] == f"Bearer {TOKEN}" for r in live.requests)
    assert all(TOKEN not in str(r.url) and TOKEN.encode() not in r.content for r in live.requests)


def test_a_user_site_repo_has_the_short_url():
    assert workspace._pages_url("Quielq/quielq.github.io", "demo") == "https://quielq.github.io/demo/"


@pytest.mark.parametrize(("folder", "site", "error"), [
    ("../outside", "demo", "outside"), ("missing", "demo", "no folder"), (".", "demo", "whole workspace"),
    ("site", "Demo Site", "lower-case"), ("site", "../x", "lower-case"), ("bare", "demo", "index.html"),
])  # fmt: skip
def test_it_refuses_bad_folders_and_names(live, folder, site, error):
    _site()
    workspace.write_file("bare/page.html", "x")
    assert error in workspace.publish_site(folder, site)["error"]
    assert live.requests == []


def test_it_never_follows_a_symlink_out(live, tmp_path_factory):
    _site()
    outside = tmp_path_factory.mktemp("outside")
    (outside / "secret.html").write_text("secret")
    (common.workspace_dir() / "site" / "secret.html").symlink_to(outside / "secret.html")
    (common.workspace_dir() / "site" / "linked").symlink_to(outside)
    workspace.publish_site("site", "demo")
    assert b"secret" not in b"".join(live.blobs.values())


def test_size_and_count_limits_hold(live, monkeypatch):
    _site()
    monkeypatch.setattr(workspace, "PAGES_MAX_FILES", 1)
    assert "over the limit" in workspace.publish_site("site", "demo")["error"]
    monkeypatch.setattr(workspace, "PAGES_MAX_FILES", 200)
    monkeypatch.setattr(workspace, "PAGES_MAX_SITE_BYTES", 5)
    assert "over 25 MB" in workspace.publish_site("site", "demo")["error"]
    assert live.requests == []


@pytest.mark.parametrize(("status", "error"), [
    ({"/git/ref/": 409}, "is empty"), ({"/git/blobs": 403}, "refused the token"), ({"/git/refs/": 422}, "changed"),
])  # fmt: skip
def test_github_errors_are_spoken_without_the_token(live, status, error):
    live.status = status
    _site()
    result = workspace.publish_site("site", "demo")
    assert error in result["error"] and TOKEN not in result["error"]


def test_a_missing_setup_is_an_error(live, monkeypatch):
    _site()
    monkeypatch.delenv("ME3_PAGES_TOKEN")
    assert "ME3_PAGES_TOKEN" in workspace.publish_site("site", "demo")["error"]
    monkeypatch.setenv("ME3_PAGES_TOKEN", TOKEN)
    monkeypatch.setenv("ME3_PAGES_REPO", "not a repo")
    assert "ME3_PAGES_REPO" in workspace.publish_site("site", "demo")["error"]


def test_the_daily_cap_stops_publishing(live):
    _site()
    for _ in range(20):
        assert workspace.publish_site("site", "demo")["published"]
    assert "cap" in workspace.publish_site("site", "demo")["error"]


def test_the_token_reaches_only_the_workspace_server_and_never_a_command(monkeypatch):
    monkeypatch.setenv("ME3_PAGES_TOKEN", TOKEN)
    assert _server_env(None, "servers.tasks.workspace.server")["ME3_PAGES_TOKEN"] == TOKEN
    for module in ("servers.tasks.comms.server", "servers.tasks.knowledge.server", "servers.tasks.delegate.server"):
        assert "ME3_PAGES_TOKEN" not in _server_env(None, module)
    assert TOKEN not in workspace._command_env().values()


def test_bash_still_cannot_push():
    for command in ("git push origin main", "gh repo create x", "curl https://api.github.com"):
        assert workspace.check(command).verdict == workspace.REFUSE


def test_the_harness_reads_back_the_real_arguments():
    rule = POLICY["tools"]["publish_site"]
    text = readback_text(rule["readback"], {"folder": "portfolio", "site": "my-site"}, POLICY["readback_max_chars"])
    assert text == ("Publish the folder portfolio on the public web as my-site. "
                    "Anyone with the link can see it. Should I do that?")  # fmt: skip
    assert "publish_site" in POLICY["containment"]["tools"]


# open_published_site: the published link opens by name, and only for a site this device published.


@pytest.fixture
def browser(monkeypatch):
    """A live workspace with a fake opener and a page that is already up."""
    monkeypatch.setenv("ME3_WORKSPACE_LIVE", "1")
    monkeypatch.setenv("SPOTIFY_CLIENT_SECRET", "hidden")
    seen = {"up": []}
    monkeypatch.setattr(workspace.subprocess, "run", lambda argv, env, **kw: seen.update(argv=argv, env=env, kw=kw))
    monkeypatch.setattr(workspace, "_site_is_up", lambda url: seen["up"].append(url) or True)
    return seen


def test_it_opens_the_link_the_publish_returned(live, browser):
    _site()
    url = workspace.publish_site("site", "demo")["url"]
    assert workspace.open_published_site("demo") == {"opened": True, "url": url}
    assert browser["argv"][1:] == [url] and browser["up"] == [url]
    assert browser["kw"].get("shell", False) is False
    assert TOKEN not in browser["env"].values() and "hidden" not in browser["env"].values()


def test_it_refuses_a_site_this_device_never_published(live, browser):
    for site in ("demo", "https://evil.example", "../x", "Demo"):
        assert "error" in workspace.open_published_site(site)
    assert "argv" not in browser and browser["up"] == []


def test_a_mock_publish_leaves_nothing_to_open(browser):
    _site()
    workspace.publish_site("site", "demo")
    assert "Publish it first" in workspace.open_published_site("demo")["error"]
    assert "argv" not in browser


def test_opening_is_a_mock_until_the_workspace_is_live(live, monkeypatch):
    _site()
    workspace.publish_site("site", "demo")
    monkeypatch.setattr(workspace.subprocess, "run", lambda *a, **k: pytest.fail("the opener ran"))
    monkeypatch.setattr(workspace, "_site_is_up", lambda url: pytest.fail("the network was used"))
    assert workspace.open_published_site("demo")["ran"] is False


def test_it_waits_for_pages_and_does_not_open_a_missing_page(live, browser, monkeypatch):
    _site()
    workspace.publish_site("site", "demo")
    monkeypatch.setattr(workspace, "_site_is_up", lambda url: False)
    result = workspace.open_published_site("demo")
    assert result["opened"] is False and "still building" in result["note"]
    assert "argv" not in browser


def test_the_wait_asks_again_until_the_page_answers(monkeypatch):
    codes = iter([404, 404, 200])
    asked = []

    def handler(request):
        asked.append(str(request.url))
        return httpx.Response(next(codes))

    real = httpx.Client
    monkeypatch.setattr(workspace.httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    monkeypatch.setattr(workspace.time, "sleep", lambda s: None)
    assert workspace._site_is_up("https://quielq.github.io/me3-sites/demo/") is True
    assert len(asked) == 3


def test_the_harness_reads_back_the_site_before_opening():
    rule = POLICY["tools"]["open_published_site"]
    text = readback_text(rule["readback"], {"site": "my-site"}, POLICY["readback_max_chars"])
    assert text == "Open the published site my-site in your browser. Should I do that?"
    assert "open_published_site" in POLICY["containment"]["tools"]
