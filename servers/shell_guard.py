"""Which shell commands may run (ADR-024). The sandbox is the boundary, and this is the layer
that speaks first: it refuses the obvious before anyone is asked, and says why.

OnIt's parser reads every executable in a command, and anything it cannot read is refused.
The harness checks before the read-back, and the workspace server checks again before it runs.
An interpreter on the list can still do anything, which is why servers/sandbox.py exists.
"""

from __future__ import annotations

import re
from pathlib import Path

from servers.vendor.onit_command_policy import ALLOW, ASK, CRITICAL, DEFAULT_ALLOWED_COMMANDS, DENY, Decision, evaluate_command

__all__ = ["ALLOW", "ASK", "CRITICAL", "DENY", "Decision", "check"]

# A yes can run something off the list, so these stay off it: the sandbox has no network
# for them, and a refusal costs less than a command that fails after a spoken yes.
ALLOWED = DEFAULT_ALLOWED_COMMANDS - {"curl", "wget", "gh", "onit", "openssl", "kill", "nvidia-smi", "nvcc"}

# These reach for the desktop, which the sandbox closes off, so a yes could only end in a failure.
NEEDS_DESKTOP = frozenset({"open", "xdg-open", "osascript"})

# Refused whatever the parser says, and as critical. From OnIt's bash server.
CATASTROPHIC = [
    (re.compile(p, re.IGNORECASE), what)
    for p, what in (
        (r"\brm\s+(-\S*\s+)*-\S*r\S*\s+(-\S*\s+)*[/~]", "recursive delete from the root or home"),
        (r">\s*/dev/(sd|hd|nvme|vd|xvd|disk|mem)", "writing to a device"),
        (r"\bdd\b.*\bof\s*=\s*/dev/", "writing to a device"),
        (r"\b(mkfs|fdisk|parted|diskutil)\b", "changing disks"),
        (r"\b(shutdown|reboot|poweroff|halt)\b", "stopping the machine"),
        (r"\b(insmod|rmmod|modprobe|kextload)\b", "changing the kernel"),
        (r"\bsysctl\s+-w\b", "changing the kernel"),
        (r"\b(csrutil|spctl|tccutil|defaults\s+write)\b", "changing system security"),
        (r"\bsecurity\s+(find|dump|add|delete|export)", "reading the keychain"),
        (r"(^|[\s/])\.env\b", "reading secrets"),
        (r"\.ssh\b", "reading secrets"),
    )
]


def check(command: str) -> Decision:
    """Allow, ask a person, or deny. Only an unlisted program is worth asking about."""
    for pattern, what in CATASTROPHIC:
        if pattern.search(command):
            return Decision(DENY, f"Blocked: {what}.", severity=CRITICAL)
    decision = evaluate_command(command, ALLOWED, ask_scope=frozenset({"command"}))
    if decision.verdict == ASK and NEEDS_DESKTOP.intersection(Path(s).name for s in decision.subjects):
        reason = "The shell has no desktop. Use open_in_browser for an HTML file. Nothing here opens other apps."
        return Decision(DENY, reason, subjects=decision.subjects)
    return decision
