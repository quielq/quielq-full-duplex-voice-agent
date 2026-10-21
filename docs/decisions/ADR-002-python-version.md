# ADR-002: Python 3.13 on both the Mac and the Pi

Date: Oct 21, 2026. Status: accepted.

## Context

The Claude Code prompt says Python 3.12 on the Mac and 3.13 on Pi OS Trixie. The architecture review (rule 4, "No laptop-only dependencies") says to use the same Python minor version on both machines. The review is the source of truth when the two disagree.

## Decision

Use Python 3.13 on both machines, installed and managed by uv (`.python-version` is `3.13`; `requires-python = ">=3.13,<3.14"`).

## Alternatives

* 3.12 on the Mac and 3.13 on the Pi. Rejected: two minor versions can resolve different wheels and hide Pi-only failures until Phase 2.
* 3.12 on both. Rejected: Trixie ships 3.13, and uv can install either, so matching the Pi is the cheaper direction.

## Consequences

All pinned packages have 3.13 wheels for macOS arm64 (verified by install). Linux aarch64 wheels still need checking on the Pi (onnxruntime, PyAudio build against portaudio19-dev).

## Sources

* docs/architecture.md, "Rules that keep the move cheap", rule 4
* docs/prompt.md, "Hard technical rules", Python versions
