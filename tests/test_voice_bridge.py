"""Step 6: VoiceMCPBridge receives voice events from the stub servers over stdio and HTTP."""

import asyncio

import pytest
from pipecat.frames.frames import EndFrame, Frame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineWorker
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.workers.runner import WorkerRunner

from bench.voice_bridge_probe import LISTEN, SPEAK, probe
from harness.voice_bridge import VoiceEventFrame, VoiceMCPBridge, VoiceServer


@pytest.mark.parametrize("transport", ["stdio", "http"])
async def test_barge_in_rehearsal(transport):
    r = await probe(transport)
    assert r["events"] >= 10
    assert r["event_hop_ms_p95"] < 50, r  # review: the extra hop must cost under a few tens of ms
    assert r["duck_rtt_ms"][0] < 50 and r["stop_rtt_ms"][0] < 50, r
    assert r["words_played_ducked"] >= 1, r  # played_up_to reports the ducked gain
    assert r["stopped_at_word"] < 39, r  # playback stopped before the end


class _Collect(FrameProcessor):
    def __init__(self):
        super().__init__()
        self.events: list[VoiceEventFrame] = []

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, VoiceEventFrame):
            self.events.append(frame)
        await self.push_frame(frame, direction)


async def test_events_reach_the_pipeline_as_frames():
    bridge = VoiceMCPBridge(VoiceServer("speak", module=SPEAK), VoiceServer("listen", module=LISTEN))
    collect = _Collect()
    worker = PipelineWorker(Pipeline([bridge, collect]), idle_timeout_secs=None)
    runner = WorkerRunner(handle_sigint=False)
    await runner.add_workers(worker)

    async def drive():
        await bridge.connect()
        await asyncio.sleep(0.2)
        await asyncio.gather(
            bridge.speak("one two three", ms_per_word=50),
            bridge.listen(script="hello", lead_ms=0, ms_per_word=50),
        )
        await worker.queue_frame(EndFrame())

    driver = asyncio.create_task(drive())
    await asyncio.wait_for(runner.run(), 30)
    await driver

    names = [(f.server, f.event) for f in collect.events]
    assert ("listen", "speech_started") in names
    assert ("speak", "played_up_to") in names
    played = [f.data for f in collect.events if f.event == "played_up_to"]
    assert [p["word_index"] for p in played] == [0, 1, 2]
    assert played[-1]["ms_offset"] == 150
