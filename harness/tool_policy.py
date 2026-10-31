"""Harness-enforced tool policy between MCPClient and the LLM (ADR-009, ADR-010).

Pipecat 1.13 has no policy hook, so ``ToolPolicy.tools`` rebuilds each FunctionSchema with a
``PolicyHandler`` around the handler ``MCPClient.tools()`` attached. Hints come from the
MCP session, because MCPClient drops them when it converts schemas.

Held actions never keep a tool call open. The handler answers the LLM at once ("waiting_for_undo" or
"needs_confirmation"), so the mic and the reply are free, and the policy owns the wait. The
outcome then replaces that tool message in the context, so the LLM sees one current truth
with no new message role and no async-tool text in the system prompt.

Only the commit step is protected. An interruption cannot touch a held action (only a
transcript can), and once a write has started it finishes even if the user barges in; the
real result then replaces the "CANCELLED" that Pipecat writes for the cancelled handler.

``PolicyGate`` runs the transcript check. It sits before the DuckingProcessor because
that one drops short finals as backchannels, and in ``immediate`` mode nothing else
listens for "cancel".

Every decision is one ``tool_policy`` event in the event log, including denials the servers
never see. The undo and confirm waits are outside the path to the first reply, so latency
reports need no correction for them.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import json
import re
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any

from loguru import logger
from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.adapters.schemas.tools_schema import ToolsSchema
from pipecat.frames.frames import (
    Frame,
    FunctionCallResultProperties,
    InterimTranscriptionFrame,
    TranscriptionFrame,
    TTSSpeakFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.services.llm_service import FunctionCallParams
from pipecat.services.mcp_service import MCPClient

PERMISSIONS = ("auto", "confirm", "disabled")
HINT_FIELDS = ("read_only_hint", "destructive_hint", "idempotent_hint", "open_world_hint")


class DuplicateToolName(RuntimeError):
    """Two servers advertise one tool name. Pipecat flattens names, so one would be lost."""


@dataclass(frozen=True)
class ToolRule:
    name: str
    permission: str
    undo_s: float
    timeout_s: float
    read_only: bool = False
    source: str = "hints"


def default_rule(name: str, hints: dict | None, defaults: dict) -> ToolRule:
    """The ADR-009 table. A missing hint needed for the decision counts as missing hints."""
    timeout_s = defaults["timeout_s"]
    undo_s = defaults["undo_s"]
    hints = hints or {}
    read_only = hints.get("read_only_hint")
    destructive = hints.get("destructive_hint")
    open_world = hints.get("open_world_hint")
    if read_only:
        return ToolRule(name, "auto", 0.0, timeout_s, read_only=True)
    if read_only is None or destructive is None:
        return ToolRule(name, "confirm", 0.0, timeout_s, source="hints_missing")
    if not destructive:
        return ToolRule(name, "auto", 0.0, timeout_s)
    if open_world is None:
        return ToolRule(name, "confirm", 0.0, timeout_s, source="hints_missing")
    return ToolRule(name, "confirm" if open_world else "auto", undo_s, timeout_s)


def apply_override(rule: ToolRule, override: dict | None) -> ToolRule:
    if not override:
        return rule
    unknown = set(override) - {"permission", "undo_s", "timeout_s"}
    if unknown:
        raise ValueError(f"{rule.name}: unknown tool policy keys {sorted(unknown)}")
    permission = override.get("permission", rule.permission)
    if permission not in PERMISSIONS:
        raise ValueError(f"{rule.name}: permission must be one of {PERMISSIONS}, not {permission!r}")
    return dataclasses.replace(
        rule,
        permission=permission,
        undo_s=float(override.get("undo_s", rule.undo_s)),
        timeout_s=float(override.get("timeout_s", rule.timeout_s)),
        source="override",
    )


def _word_re(words: Sequence[str]) -> re.Pattern:
    if not all(isinstance(w, str) for w in words):
        raise TypeError(f"policy words must be strings (quote 'no' in YAML): {words!r}")
    return re.compile(r"\b(" + "|".join(map(re.escape, words)) + r")\b", re.I)


async def tool_hints(client: MCPClient) -> dict[str, dict | None]:
    """Read MCP annotations from the live session. ``_active_session`` is private, but Pipecat
    is pinned exactly and tests/test_tool_policy.py fails if this stops working."""
    listed = await client._active_session.list_tools()
    out: dict[str, dict | None] = {}
    for tool in listed.tools:
        ann = tool.annotations
        out[tool.name] = {f: getattr(ann, f, None) for f in HINT_FIELDS} if ann else None
    return out


@dataclass
class HeldAction:
    rule: ToolRule
    inner: Callable[[FunctionCallParams], Awaitable[None]]
    params: FunctionCallParams
    state: str  # confirm | undo | committing
    task: asyncio.Task | None = None
    outcome: dict | None = None
    in_context: bool = False


@dataclass
class Verdict:
    """What a transcript did to the held actions."""

    decision: str  # undone | confirmed | denied
    phrase: str


class PolicyHandler:
    """Stands in for MCPClient's handler. The cleanup is carried over so the LLM service
    still closes the MCP connection at teardown."""

    def __init__(self, policy: ToolPolicy, rule: ToolRule, inner):
        self._policy = policy
        self.rule = rule
        self.inner = inner
        self._pipecat_cleanup = inner._pipecat_cleanup

    async def __call__(self, params: FunctionCallParams) -> None:
        await self._policy.handle(self.rule, self.inner, params)


class ToolPolicy:
    def __init__(self, config: dict, on_event: Callable[..., object] | None = None):
        self._cfg = config
        self._on_event = on_event or (lambda *a, **k: None)
        self._undo_re = _word_re(config["undo_words"])
        self._yes_re = _word_re(config["yes_words"])
        self._no_re = _word_re(config["no_words"])
        self.phrases: dict[str, str] = config["phrases"]
        self.answer_max_words: int = config["answer_max_words"]
        self._yes_max_words: int = config["yes_max_words"]
        self.rules: dict[str, ToolRule] = {}
        self.decisions: list[dict] = []
        self._held: list[HeldAction] = []
        self._background: set[asyncio.Task] = set()
        # Set by the bot once the pipeline exists; speaks a fixed phrase.
        self.speak: Callable[[str], Awaitable[None]] | None = None

    def _decide(self, tool: str, decision: str, **fields: Any) -> None:
        rec = {"tool": tool, "decision": decision, **fields}
        self.decisions.append(rec)
        self._on_event("tool_policy", **rec)

    async def tools(self, clients: dict[str, MCPClient]) -> ToolsSchema:
        """Merge the servers' tools under policy. Raises DuplicateToolName before anything runs."""
        owners: dict[str, str] = {}
        kept: list[FunctionSchema] = []
        overrides = self._cfg.get("tools") or {}
        for server, client in clients.items():
            schema = await client.tools()
            hints = await tool_hints(client)
            for tool in schema.standard_tools:
                if tool.name in owners:
                    raise DuplicateToolName(
                        f"{tool.name!r} is on both {owners[tool.name]} and {server}"
                    )
                owners[tool.name] = server
                rule = default_rule(tool.name, hints.get(tool.name), self._cfg["defaults"])
                rule = apply_override(rule, overrides.get(tool.name))
                self.rules[tool.name] = rule
                self._decide(tool.name, "registered", server=server, permission=rule.permission,
                             undo_s=rule.undo_s, timeout_s=rule.timeout_s, source=rule.source)  # fmt: skip
                if rule.permission == "disabled":
                    self._decide(tool.name, "removed_from_schema", server=server)
                    continue
                kept.append(
                    FunctionSchema(
                        name=tool.name,
                        description=tool.description,
                        properties=tool.properties,
                        required=tool.required,
                        handler=PolicyHandler(self, rule, tool.handler),
                    )
                )
        for name in set(overrides) - set(owners):
            logger.warning(f"tool policy names {name!r}, which no connected server offers")
            self._decide(name, "override_unused")
        return ToolsSchema(standard_tools=kept)

    async def deny_unknown(self, params: FunctionCallParams) -> None:
        """Catch-all handler: a model that calls a disabled or invented tool reaches no server."""
        self._decide(params.function_name, "denied_unknown", args=dict(params.arguments))
        await params.result_callback(
            {"status": "denied", "reason": f"{params.function_name} is not available"}
        )

    async def handle(self, rule: ToolRule, inner, params: FunctionCallParams) -> None:
        args = dict(params.arguments)
        if rule.permission == "disabled":  # removed from the schema, so only a stale call lands here
            self._decide(rule.name, "denied_disabled", args=args)
            await params.result_callback({"status": "denied", "reason": "disabled"})
        elif rule.permission == "confirm":
            await self._hold(rule, inner, params, "confirm")
        elif rule.undo_s > 0:
            await self._hold(rule, inner, params, "undo")
        elif rule.read_only:
            self._decide(rule.name, "allowed", args=args)
            await params.result_callback(await self._call(rule, inner, params))
        else:
            self._decide(rule.name, "allowed", args=args)
            await self._protected_commit(rule, inner, params)

    async def _hold(self, rule: ToolRule, inner, params: FunctionCallParams, state: str) -> None:
        for old in [a for a in self._held if a.rule.name == rule.name and a.state == state]:
            await self._drop(old, "superseded")
        action = HeldAction(rule, inner, params, state)
        self._held.append(action)
        self._decide(rule.name, f"hold_{state}", args=dict(params.arguments), undo_s=rule.undo_s)
        if state == "confirm":
            action.task = self._spawn(self._confirm_expiry(action))
            result = {
                "status": "needs_confirmation",
                "note": "Not done. Ask the user to confirm in one short question, then stop. "
                "Their yes or no is heard directly, so do not call this tool again.",
            }
        else:
            action.task = self._spawn(self._undo_window(action))
            result = {
                "status": "waiting_for_undo",
                "runs_in_seconds": rule.undo_s,
                "note": "Not done yet. Tell the user in one short sentence that it happens in "
                f"{rule.undo_s:g} seconds unless they say cancel. Do not call this tool again.",
            }

        async def reached_context() -> None:
            action.in_context = True
            self._write_back(action)

        await params.result_callback(
            result, properties=FunctionCallResultProperties(on_context_updated=reached_context)
        )

    def _spawn(self, coro) -> asyncio.Task:
        task = asyncio.create_task(coro)
        self._background.add(task)
        task.add_done_callback(self._background.discard)
        return task

    async def _confirm_expiry(self, action: HeldAction) -> None:
        await asyncio.sleep(self._cfg["defaults"]["confirm_wait_s"])
        self._finish(action, "denied_timeout", {"status": "not_done", "reason": "no answer"})

    async def _undo_window(self, action: HeldAction) -> None:
        await asyncio.sleep(action.rule.undo_s)
        await self._commit(action)

    async def _commit(self, action: HeldAction) -> None:
        action.state = "committing"
        result = await self._call(action.rule, action.inner, action.params)
        failed = _is_failure(result)
        self._finish(action, "commit_failed" if failed else "committed", {"status": "failed" if failed else "done", "result": result})  # fmt: skip
        if failed and self.speak:
            await self.speak(self.phrases["failed"])

    async def _call(self, rule: ToolRule, inner, params: FunctionCallParams) -> Any:
        """Run the real tool with the policy timeout and return its result."""
        results: list[Any] = []

        async def capture(result: Any, *, properties=None) -> None:
            results.append(result)

        try:
            async with asyncio.timeout(rule.timeout_s):
                await inner(dataclasses.replace(params, result_callback=capture))
        except TimeoutError:
            self._decide(rule.name, "timeout", timeout_s=rule.timeout_s)
            return {"error": f"timed out after {rule.timeout_s:g} s"}
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self._decide(rule.name, "tool_error", error=f"{type(e).__name__}: {e}")
            return {"error": str(e)}
        return results[0] if results else {"error": "no result"}

    async def _protected_commit(self, rule: ToolRule, inner, params: FunctionCallParams) -> None:
        """A write that has started finishes. A barge-in cancels this handler, not the write."""
        task = self._spawn(self._call(rule, inner, params))
        try:
            await params.result_callback(await asyncio.shield(task))
        except asyncio.CancelledError:
            self._decide(rule.name, "commit_outlived_interruption")
            self._spawn(self._write_back_late(rule, inner, params, task))
            raise

    async def _write_back_late(self, rule, inner, params, task: asyncio.Task) -> None:
        result = await task
        # Pipecat writes "CANCELLED" when the cancel frame reaches the aggregator. Land after it.
        await asyncio.sleep(0.05)
        action = HeldAction(rule, inner, params, "committing", in_context=True)
        action.outcome = {"status": "failed" if _is_failure(result) else "done", "result": result}
        self._write_back(action)

    def _write_back(self, action: HeldAction) -> None:
        if action.outcome is None or not action.in_context:
            return
        for message in reversed(action.params.context.messages):
            if message.get("role") == "tool" and message.get("tool_call_id") == action.params.tool_call_id:
                message["content"] = json.dumps(action.outcome, ensure_ascii=False, default=str)
                return
        logger.warning(f"tool policy: no tool message left for {action.rule.name}")

    def _finish(self, action: HeldAction, decision: str, outcome: dict, **fields: Any) -> None:
        if action in self._held:
            self._held.remove(action)
        action.outcome = outcome
        self._write_back(action)
        self._decide(action.rule.name, decision, **fields)

    async def _drop(self, action: HeldAction, decision: str) -> None:
        task = action.task
        if task and not task.done() and task is not asyncio.current_task():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._finish(action, decision, {"status": "not_done", "reason": decision})

    def on_transcript(self, text: str, *, final: bool = True) -> Verdict | None:
        """Apply what the user just said to the held actions. Synchronous, so a transcript takes
        effect before anything else can run. Only a transcript can undo or confirm.

        Stopping acts on interim text, the faster and safer direction. A yes waits for the final
        transcript and must be short, so "okay, but what time is it" does not approve anything.
        """
        undoable = [a for a in self._held if a.state == "undo"]
        if undoable and self._undo_re.search(text):
            for action in undoable:
                self._end_hold(action, "undone", "the user cancelled", text)
            return Verdict("undone", self.phrases["undone"])
        for action in [a for a in self._held if a.state == "confirm"]:
            if self._no_re.search(text):
                self._end_hold(action, "denied_user", "the user declined", text)
                return Verdict("denied", self.phrases["denied"])
            if not final:
                continue
            if self._yes_re.search(text) and len(text.split()) <= self._yes_max_words:
                return self._confirm(action, text)
            # A different request is not a yes, and a later "yes" may not mean this.
            self._end_hold(action, "denied_no_answer", "the user moved on", text)
        return None

    def _end_hold(self, action: HeldAction, decision: str, reason: str, text: str) -> None:
        if action.task:
            action.task.cancel()
        self._finish(action, decision, {"status": "not_done", "reason": reason}, text=text)

    def _confirm(self, action: HeldAction, text: str) -> Verdict:
        if action.task:
            action.task.cancel()
        self._decide(action.rule.name, "confirmed", text=text)
        undo_s = action.rule.undo_s
        if undo_s > 0:
            action.state = "undo"
            action.task = self._spawn(self._undo_window(action))
            return Verdict("confirmed", self.phrases["confirmed_undo"].format(undo_s=f"{undo_s:g}"))
        action.state = "committing"
        action.task = self._spawn(self._commit(action))
        return Verdict("confirmed", self.phrases["confirmed_now"])

    async def settled(self, timeout_s: float = 30.0) -> None:
        """Wait out undo windows and commits. Unanswered confirmations do not hold a run open."""
        async with asyncio.timeout(timeout_s):
            while any(a.state != "confirm" for a in self._held):
                await asyncio.sleep(0.02)

    async def close(self) -> None:
        """End of session: nothing held may run after the agent has gone."""
        for action in list(self._held):
            if action.state == "committing":
                with contextlib.suppress(TimeoutError, asyncio.CancelledError):
                    await asyncio.wait_for(asyncio.shield(action.task), action.rule.timeout_s)
            else:
                await self._drop(action, "dropped_at_shutdown")
        for task in list(self._background):
            task.cancel()


def _is_failure(result: Any) -> bool:
    if isinstance(result, dict):
        return "error" in result
    return isinstance(result, str) and result.startswith(("Error calling mcp tool", "Sorry, could not"))


class PolicyGate(FrameProcessor):
    """Runs the policy's transcript check in the voice pipeline, in both barge-in modes.

    A hit interrupts playback. If the utterance was only the answer, its final transcript
    is dropped and the fixed phrase is spoken, so "cancel" costs no LLM call. A longer one
    goes on to the LLM.
    """

    def __init__(self, policy: ToolPolicy, on_spoken: Callable[[], object] | None = None, **kwargs):
        super().__init__(**kwargs)
        self._policy = policy
        self._on_spoken = on_spoken or (lambda: None)
        self._verdict: Verdict | None = None
        self._commit_wait_s = 0.1

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, (InterimTranscriptionFrame, TranscriptionFrame)):
            final = isinstance(frame, TranscriptionFrame)
            verdict = self._policy.on_transcript(frame.text, final=final)
            if verdict:
                self._verdict = verdict
                await self.broadcast_interruption()
            if final and self._verdict:
                held, self._verdict = self._verdict, None
                if len(frame.text.split()) <= self._policy.answer_max_words:
                    # Same ordering wait as the DuckingProcessor: the interruption settles first.
                    await asyncio.sleep(self._commit_wait_s)
                    await self.push_frame(TTSSpeakFrame(held.phrase))
                    self._on_spoken()
                    return
        await self.push_frame(frame, direction)
