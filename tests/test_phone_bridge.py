"""The Mac phone bridge and the comms server (ADR-024). Nothing is sent: subprocess is a fake."""

from __future__ import annotations

import json
import threading
from http.server import ThreadingHTTPServer
from types import SimpleNamespace

import pytest
import yaml

from harness.mcp_tools import _server_env
from scripts import mac_phone_bridge as bridge
from servers import common
from servers.tasks.comms import server as comms

TOKEN = "test-token"


@pytest.fixture
def phone(tmp_path, monkeypatch):
    """A real bridge on loopback whose commands go to a list, and comms pointed at it."""
    ran = []

    def run(cmd, **kwargs):
        ran.append(cmd)
        return SimpleNamespace(returncode=0, stderr="")

    args = bridge.parse_args([])
    http = ThreadingHTTPServer(("127.0.0.1", 0), bridge.make_handler(args, TOKEN, run))
    threading.Thread(target=http.serve_forever, daemon=True).start()
    monkeypatch.setenv("ME3_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ME3_TOOL_STAGES", "comms=bridge")
    monkeypatch.setenv("ME3_PHONE_BRIDGE_URL", f"http://127.0.0.1:{http.server_port}")
    monkeypatch.setenv("ME3_PHONE_BRIDGE_TOKEN", TOKEN)
    (tmp_path / "contacts.yaml").write_text(yaml.safe_dump({"contacts": {"Ana": "+639170000000"}}))
    yield ran
    http.shutdown()


def test_a_message_reaches_a_saved_contact(phone, tmp_path):
    out = comms.send_message("ana", "running late")
    assert out["sent"] and out["to"] == "Ana"
    assert phone[0][0] == "osascript" and phone[0][3:] == ["+639170000000", "running late", "iMessage"]
    assert "+63" not in json.dumps(out)


def test_a_call_says_the_mac_needs_a_click(phone):
    out = comms.call_contact("Ana")
    assert phone == [["open", "tel:+639170000000"]]
    assert "Call button" in out["status"]


def test_only_saved_contacts_can_be_reached(phone):
    assert "not a saved contact" in comms.send_message("+639999999999", "hi")["error"]
    assert "not a saved contact" in comms.call_contact("Bob")["error"]
    assert phone == []


def test_the_model_sees_names_and_never_numbers(phone):
    assert comms.list_contacts() == {"contacts": ["Ana"]}


def test_a_wrong_token_sends_nothing(phone, monkeypatch):
    monkeypatch.setenv("ME3_PHONE_BRIDGE_TOKEN", "wrong")
    assert "refused: 403" in comms.send_message("Ana", "hi")["error"]
    assert phone == []


def test_the_daily_limit_stops_sends(phone, monkeypatch):
    config = common._providers()
    config["providers"]["phone_bridge"]["daily_cap_usd"] = 0.001
    monkeypatch.setattr(common, "_providers", lambda: config)
    assert [("error" in comms.send_message("Ana", "hi")) for _ in range(3)] == [False, False, True]
    assert len(phone) == 2


def test_the_default_cap_allows_twenty_a_day():
    config = common._providers()
    limit = config["providers"]["phone_bridge"]["daily_cap_usd"] / config["prices"]["phone_bridge_per_action"]
    assert round(limit) == 20


def test_no_bridge_is_an_error_not_a_silent_mock(phone, monkeypatch):
    monkeypatch.delenv("ME3_PHONE_BRIDGE_URL")
    assert "not set up" in comms.send_message("Ana", "hi")["error"]


def test_a_dead_bridge_is_spoken_and_not_counted_sent(phone, monkeypatch, tmp_path):
    monkeypatch.setenv("ME3_PHONE_BRIDGE_URL", "http://127.0.0.1:1")
    assert "did not answer" in comms.send_message("Ana", "hi")["error"]
    assert comms.list_sent_messages() == {"messages": []}


def test_the_mock_stays_local(tmp_path, monkeypatch):
    monkeypatch.setenv("ME3_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ME3_TOOL_STAGES", "comms=mock")
    assert comms.send_message("Anyone", "hi")["mock"] and comms.call_contact("Anyone")["mock"]
    assert len(comms.list_sent_messages()["messages"]) == 2


def test_a_dry_run_sends_nothing(phone, monkeypatch):
    args = bridge.parse_args(["--dry-run"])
    http = ThreadingHTTPServer(("127.0.0.1", 0), bridge.make_handler(args, TOKEN, lambda *a, **k: phone.append("ran")))
    threading.Thread(target=http.serve_forever, daemon=True).start()
    monkeypatch.setenv("ME3_PHONE_BRIDGE_URL", f"http://127.0.0.1:{http.server_port}")
    out = comms.send_message("Ana", "hi")
    http.shutdown()
    assert out["dry_run"] and not out["sent"] and phone == []


def test_the_applescript_never_holds_the_message():
    evil = '" & do shell script "id" & "'
    cmd = bridge.message_command("+639170000000", evil, "iMessage")
    assert evil not in cmd[2] and cmd[4] == evil


def test_a_pi_must_be_named_to_connect():
    with pytest.raises(SystemExit):
        bridge.parse_args(["--host", "0.0.0.0"])
    assert bridge.parse_args(["--host", "0.0.0.0", "--allow-from", "192.168.1.20"])
    assert bridge.client_allowed("127.0.0.1", set())
    assert bridge.client_allowed("192.168.1.20", {"192.168.1.20"})
    assert not bridge.client_allowed("192.168.1.21", {"192.168.1.20"})


def test_the_token_reaches_only_comms(monkeypatch):
    monkeypatch.setenv("ME3_PHONE_BRIDGE_TOKEN", TOKEN)
    assert "ME3_PHONE_BRIDGE_TOKEN" in _server_env(None, "servers.tasks.comms.server")
    assert "ME3_PHONE_BRIDGE_TOKEN" not in _server_env(None, "servers.tasks.workspace.server")
    assert "ME3_PHONE_BRIDGE_TOKEN" not in _server_env(None, "servers.tasks.knowledge.server")
