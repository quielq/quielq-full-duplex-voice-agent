# ADR-001: Pinned versions for the first spike

Date: Oct 21, 2026. Status: accepted.

## Context

The review asks us to pin Pipecat, FastMCP, and the MCP Python SDK together, and to confirm whether Pipecat pull request 5624 (MCP SDK 2.x support) is merged. If it were not merged, we would have to pin `mcp<2`.

## Decision

Exact versions resolved in `uv.lock` on an Apple silicon Mac (macOS 14.3.1):

| Package | Version | Note |
| --- | --- | --- |
| Python | 3.13.15 (uv managed) | See ADR-002 |
| pipecat-ai | 1.13.0 | Extras: anthropic, cartesia, deepgram, groq, local, mcp, silero |
| fastmcp | 4.1.3 | Requires `mcp>=2.2,<3` |
| mcp | 2.3.0 | Pipecat 1.13.0's `mcp` extra requires `mcp[cli]>=2.1.1,<3` |
| onnxruntime | 1.24.4 | Silero VAD and Smart Turn v3 |
| pyaudio | 0.2.14 | Needs the portaudio system package (Homebrew portaudio 19.7.0 on the Mac) |
| numpy | 2.5.4 | |
| pydantic | 2.14.1 | |
| uv | 0.12.21 | |

Pyproject constraints: `pipecat-ai[...]==1.13.0`, `fastmcp>=4.1,<5`, `mcp>=2,<3`.

**PR 5624 is merged** (Sep 11, 2026, before Pipecat 1.10.0), so `mcp<2` is not needed. The installed Pipecat already requires MCP SDK 2.x.

**The `local-smart-turn` extra is left out on purpose.** `LocalSmartTurnAnalyzerV3` imports only numpy, onnxruntime, and soxr, and its ONNX model (`smart-turn-v3.2-cpu.onnx`) ships inside the core `pipecat-ai` wheel. The extra pulls in torch 2.14 and transformers 5.20 (about 600 MB), which v3 does not use. Without it the virtual environment is 408 MB instead of 1.0 GB, and the Smart Turn tests still pass. This matters for the Pi.

## Alternatives

* `mcp<2` with an older Pipecat. Rejected: not needed, since PR 5624 is merged.
* Keep `local-smart-turn`. Rejected: dead weight for v3 on the Pi.

## Consequences

* Gate 1 tests run on MCP SDK 2.3.0 over both stdio and streamable HTTP.
* If we ever need Smart Turn v2 or the CoreML variant, revisit the extra.
* Recheck all pins before the Pi install. Pi OS Trixie needs `portaudio19-dev` for PyAudio.

## Sources

* https://github.com/pipecat-ai/pipecat/pull/5624 (merged Sep 11, 2026)
* https://pypi.org/project/pipecat-ai/1.13.0/
* https://pypi.org/project/fastmcp/4.1.3/
* https://pypi.org/project/mcp/2.3.0/
* Installed source: `pipecat/audio/turn/smart_turn/local_smart_turn_v3.py`
