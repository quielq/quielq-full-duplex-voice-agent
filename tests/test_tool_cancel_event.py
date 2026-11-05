"""A tool cancelled by an interruption leaves a `tool_call_cancelled` event (Nov 5 weather turn)."""

import json

from pipecat.frames.frames import FunctionCallCancelFrame
from pipecat.observers.base_observer import FramePushed
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from harness.events import EventLog, TurnEventObserver


async def test_a_cancelled_tool_is_logged_once(tmp_path):
    log = EventLog(tmp_path)
    obs = TurnEventObserver(log)
    for direction in (FrameDirection.DOWNSTREAM, FrameDirection.UPSTREAM):  # a broadcast reaches both ways
        frame = FunctionCallCancelFrame(function_name="get_weather", tool_call_id="call-1")
        await obs.on_push_frame(FramePushed(source=FrameProcessor(), destination=FrameProcessor(), frame=frame,
                                            direction=direction, timestamp=0))  # fmt: skip
    log.close()
    events = [json.loads(line) for line in log.path.read_text().splitlines()]
    assert [(e["event"], e.get("name")) for e in events] == [("tool_call_cancelled", "get_weather")]
