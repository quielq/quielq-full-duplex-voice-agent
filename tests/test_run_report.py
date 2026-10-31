"""bench/run_report: anomaly rules on hand-written event logs, no bot and no spend."""

import json

import pytest

from bench.run_report import _turns, build_report, find_anomalies, find_log, timeline


def ev(event, turn=0, t_ms=0, **fields):
    return {"run_id": "r", "turn_id": turn, "event": event, "t_ns": int(t_ms * 1e6), **fields}


def codes(events):
    return {a.code for a in find_anomalies(events, _turns(events))}


GOOD = [
    ev("run_start", stages={"stt": "fake"}, input="wav"),
    ev("user_turn_start", 1, 10),
    ev("asr_final", 1, 20, text="hello"),
    ev("end_of_turn", 1, 30),
    ev("tts_request", 1, 100),
    ev("first_audio_out", 1, 530),
    ev("audio_silent", 1, 900),
    ev("run_end", 1, 950, session_usd=0.0),
]


def test_a_good_run_is_clean_and_timed():
    assert codes(GOOD) == set()
    assert _turns(GOOD)[0].reply_ms == 500


def test_missing_run_end_is_reported():
    assert "no_run_end" in codes(GOOD[:-1])


@pytest.mark.parametrize(
    "extra,code",
    [
        (ev("pipeline_error", 1, 5, error="Tool call validation failed"), "pipeline_error"),
        (ev("reply_timeout", 1, 5), "reply_timeout"),
        (ev("spend_cap_reached", 1, 5, provider="groq"), "spend_cap"),
        (ev("tool_policy", 1, 5, tool="x", decision="tool_error", error="boom"), "tool_failed"),
        (ev("tts_cleared", 1, 5, clear_ms=3875, timed_out=True), "tts_clear_timeout"),
        (ev("echo_window", 1, 5, max_output_gap_ms=900), "output_gap"),
    ],
)
def test_single_event_anomalies(extra, code):
    assert code in codes([*GOOD, extra])


def test_a_heard_turn_with_no_speech_is_silent():
    events = [ev("user_turn_start", 1), ev("asr_final", 1, 1, text="hello"), ev("run_end", 1, 2)]
    assert "silent_turn" in codes(events)


def test_a_turn_answered_by_the_policy_is_not_silent():
    events = [ev("user_text", 1, text="yes"), ev("tool_policy", 1, 1, decision="confirmed"), ev("run_end", 1, 2)]
    assert "silent_turn" not in codes(events)


def test_a_turn_with_no_transcript_is_a_phantom():
    assert "phantom_turn" in codes([*GOOD, ev("user_turn_start", 2, 960)])


def hop(arrived, backlog, turn=1):
    return ev("audio_hop", turn, 1, window_ms=1000, arrived_ms=arrived, written_ms=400, backlog_ms=backlog)


def test_starved_speech_needs_low_arrival_and_no_backlog():
    assert "tts_starved" in codes([*GOOD, hop(430, 0), hop(430, 10)])
    assert "tts_starved" not in codes([*GOOD, hop(430, 5000), hop(430, 5000)])  # buffered, plays fine
    assert "tts_starved" not in codes([*GOOD, hop(430, 0)])  # one window is the tail of a reply


def test_interruption_counts_only_when_it_cuts_speech():
    idle = [*GOOD, ev("user_turn_start", 2, 960), ev("interruption", 2, 960)]
    assert not any(t.interrupted for t in _turns(idle))
    cut = [*GOOD[:6], ev("user_turn_start", 2, 700), ev("interruption", 2, 700)]
    assert _turns(cut)[0].interrupted


def test_a_truncated_log_still_reads(tmp_path):
    path = tmp_path / "events-20260101-000000-abc.jsonl"
    path.write_text("\n".join(json.dumps(e) for e in GOOD[:3]) + '\n{"run_id": "r", "tu')
    report = build_report(path)
    assert not report.finished and len(report.turns) == 1


def test_find_log_picks_latest_and_filters_live(tmp_path):
    fake = {"event": "run_start", "stages": {"stt": "fake"}}
    live = {"event": "run_start", "stages": {"stt": "deepgram_flux"}}
    (tmp_path / "events-20260101-000000-aaa.jsonl").write_text(json.dumps(live) + "\n")
    (tmp_path / "events-20260102-000000-bbb.jsonl").write_text(json.dumps(fake) + "\n")
    assert "bbb" in find_log(None, logs_dir=tmp_path).name
    assert "aaa" in find_log(None, live=True, logs_dir=tmp_path).name
    assert "aaa" in find_log("aaa", logs_dir=tmp_path).name
    with pytest.raises(SystemExit):
        find_log("zzz", logs_dir=tmp_path)


def test_timeline_hides_noise_unless_asked():
    events = [*GOOD, hop(1000, 0)]
    assert not any("audio_hop" in line for line in timeline(events))
    assert any("audio_hop" in line for line in timeline(events, show_all=True))
