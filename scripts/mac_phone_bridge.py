#!/usr/bin/env python
"""Run on the Mac: lets ME3, on this Mac or on the Pi, send iMessages and start phone calls
through your iPhone. Standard library only, adapted from quielq-vcm (ADR-024).

    uv run --env-file .env python scripts/mac_phone_bridge.py --dry-run         # sends nothing
    uv run --env-file .env python scripts/mac_phone_bridge.py                   # this Mac only
    uv run --env-file .env python scripts/mac_phone_bridge.py --host 0.0.0.0 --allow-from <pi ip>

The token comes from ME3_PHONE_BRIDGE_TOKEN, never from the command line, where `ps` would show it.

Mac setup, once:
- Messages signed in to your Apple ID. For SMS to non-iPhones, turn on iPhone Settings, Messages,
  Text Message Forwarding for this Mac, and run with --service SMS.
- Calls: iPhone Settings, Phone, Calls on Other Devices, allow this Mac. Then FaceTime on the
  Mac, Settings, "Calls from iPhone".
- The first message makes macOS ask whether Terminal may control Messages. Click Allow.
- A call opens the macOS call prompt, and someone must click Call. macOS has no fully hands-free way.

The Pi talks plain HTTP over the LAN, so the token and each message are readable on that
network. Use a network you trust, and name the Pi with --allow-from.
"""

from __future__ import annotations

import argparse
import hmac
import ipaddress
import json
import os
import re
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# Arguments reach AppleScript as argv, never spliced into the script, so a message cannot inject code.
SEND_SCRIPT = """
on run argv
    set theNumber to item 1 of argv
    set theText to item 2 of argv
    set theServiceType to item 3 of argv
    tell application "Messages"
        if theServiceType is "SMS" then
            set theService to 1st account whose service type = SMS
        else
            set theService to 1st account whose service type = iMessage
        end if
        send theText to participant theNumber of theService
    end tell
end run
"""
NUMBER_RE = re.compile(r"^\+?[0-9 ()-]{5,20}$")
MAX_BODY_BYTES = 8192
MAX_TEXT_CHARS = 1000


def message_command(number: str, text: str, service: str) -> list[str]:
    return ["osascript", "-e", SEND_SCRIPT, number, text, service]


def call_command(number: str) -> list[str]:
    return ["open", "tel:" + re.sub(r"[^0-9+]", "", number)]


def client_allowed(address: str, allowed: set[str]) -> bool:
    return ipaddress.ip_address(address).is_loopback or address in allowed


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--service", choices=["iMessage", "SMS"], default="iMessage")
    ap.add_argument("--allow-from", action="append", default=[], help="an address other than this Mac that may connect")
    ap.add_argument("--dry-run", action="store_true", help="say what would run, and run nothing")
    args = ap.parse_args(argv)
    if not ipaddress.ip_address(args.host).is_loopback and not args.allow_from:
        ap.error("--host outside this Mac needs --allow-from <pi ip>")
    return args


def make_handler(args: argparse.Namespace, token: str, run=subprocess.run) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def _reply(self, code: int, body: dict) -> None:
            data = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _permitted(self) -> bool:
            if not client_allowed(self.client_address[0], set(args.allow_from)):
                self._reply(403, {"error": "address not allowed"})
                return False
            if not hmac.compare_digest(self.headers.get("X-Bridge-Token", ""), token):
                self._reply(403, {"error": "bad token"})
                return False
            return True

        def do_GET(self):  # noqa: N802
            if self._permitted():
                self._reply(200, {"status": "ok", "dry_run": args.dry_run, "service": args.service})

        def do_POST(self):  # noqa: N802
            if not self._permitted():
                return None
            length = int(self.headers.get("Content-Length", 0))
            if length > MAX_BODY_BYTES:
                return self._reply(413, {"error": "too long"})
            try:
                payload = json.loads(self.rfile.read(length) or b"{}")
            except json.JSONDecodeError:
                return self._reply(400, {"error": "bad json"})
            number = str(payload.get("number", ""))
            if not NUMBER_RE.match(number):
                return self._reply(400, {"error": "bad number"})
            if self.path == "/message":
                cmd = message_command(number, str(payload.get("text", ""))[:MAX_TEXT_CHARS], args.service)
                status = f"message sent via {args.service}"
            elif self.path == "/call":
                cmd = call_command(number)
                status = "call waiting on the Mac: click the Call button"
            else:
                return self._reply(404, {"error": "unknown path"})
            contact = payload.get("contact", "a contact")
            if args.dry_run:
                print(f"dry run: {self.path[1:]} to {contact}")
                return self._reply(200, {"status": f"dry run: {status}", "dry_run": True})
            result = run(cmd, capture_output=True, text=True, timeout=20)
            if result.returncode != 0:
                return self._reply(500, {"error": result.stderr.strip()[:300]})
            print(f"{self.path[1:]} to {contact}: {status}")  # never the text or the number
            return self._reply(200, {"status": status})

        def log_message(self, *a):
            pass

    return Handler


def main() -> None:
    args = parse_args()
    token = os.environ.get("ME3_PHONE_BRIDGE_TOKEN", "")
    if not token:
        sys.exit("Set ME3_PHONE_BRIDGE_TOKEN in .env, for example from `openssl rand -hex 16`.")
    print(f"Phone bridge on {args.host}:{args.port} ({args.service}{', dry run' if args.dry_run else ''}). Ctrl+C stops it.")
    ThreadingHTTPServer((args.host, args.port), make_handler(args, token)).serve_forever()


if __name__ == "__main__":
    main()
