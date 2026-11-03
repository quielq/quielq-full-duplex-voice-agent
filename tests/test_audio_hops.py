"""Per-second comparison of audio arriving and audio written (no hardware, no spend)."""

from types import SimpleNamespace

from harness.audio_hops import AudioHops, trace_writes


class Clock:
    now = 0.0

    def __call__(self):
        return self.now


def _hops():
    events, clock = [], Clock()
    return AudioHops(lambda name, **f: events.append((name, f)), clock=clock), events, clock


def test_reports_speech_durations_each_second():
    hops, events, clock = _hops()
    hops.on_arrival(32000, 16000)  # 1 s of speech
    clock.now = 0.5
    hops.on_write(9600, 24000, 0.04)  # 200 ms of speech
    assert events == []
    clock.now = 1.0
    hops.on_write(9600, 24000, 0.3)
    name, fields = events[0]
    assert name == "audio_hop"
    assert fields["arrived_ms"] == 1000 and fields["written_ms"] == 400
    assert fields["writes"] == 2 and fields["write_max_ms"] == 300 and fields["write_busy_ms"] == 340
    assert fields["backlog_ms"] == 600


def test_arrival_gap_is_reported_but_a_quiet_bot_is_not():
    hops, events, clock = _hops()
    hops.on_arrival(320, 16000)
    clock.now = 0.4
    hops.on_arrival(320, 16000)
    clock.now = 1.0
    hops.on_arrival(320, 16000)
    assert events[0][1]["arrival_max_gap_ms"] == 600
    clock.now = 60.0
    hops.on_arrival(320, 16000)
    clock.now = 61.0
    hops.on_write(320, 16000, 0.01)
    assert events[1][1]["arrival_max_gap_ms"] == 0


def test_a_wait_between_replies_is_not_slow_speech():
    hops, events, clock = _hops()
    hops.on_write(9600, 24000, 0.04)
    clock.now = 0.5
    hops.on_write(9600, 24000, 0.04)  # the reply ends here
    clock.now = 15.0
    hops.on_arrival(3200, 16000)
    assert len(events) == 1 and events[0][1]["window_ms"] == 500 and events[0][1]["written_ms"] == 400
    clock.now = 16.0
    hops.on_arrival(32000, 16000)
    assert events[1][1]["window_ms"] == 1000 and events[1][1]["arrived_ms"] == 1100


def test_interruption_clears_the_backlog():
    hops, events, clock = _hops()
    hops.on_arrival(32000, 16000)
    hops.reset()
    clock.now = 1.0
    hops.on_arrival(320, 16000)
    assert events[0][1]["backlog_ms"] == 10


async def test_trace_writes_times_the_transport_write():
    hops, events, clock = _hops()

    async def write(frame):
        return True

    output = SimpleNamespace(write_audio_frame=write)
    trace_writes(output, hops)
    frame = SimpleNamespace(audio=bytes(960), sample_rate=24000)
    assert await output.write_audio_frame(frame) is True
    clock.now = 1.0
    await output.write_audio_frame(frame)
    assert events[0][1]["written_ms"] == 40 and events[0][1]["writes"] == 2
