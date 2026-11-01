"""End-of-turn sweep scoring, from scripted event lists (no audio, no spend)."""

from bench.eot_sweep import clip_row, summarise


def _ev(name, ms, **fields):
    return {"event": name, "t_ns": int(ms * 1e6), **fields}


def test_wait_counts_from_the_last_loud_sample():
    events = [
        _ev("injection_speech_end", 1500),
        _ev("asr_final", 2300, text="what time is it", trigger="eot"),
        _ev("end_of_turn", 2300),
        _ev("llm_request", 2310),
        _ev("tts_first_byte", 2900),
    ]
    row = clip_row(events, pad_s=0.5, text="what time is it")
    assert row["eot_ms"] == 1300
    assert row["reply_ms"] == 1900
    assert not row["cut"]
    assert row["llm_calls"] == 1


def test_turn_ending_inside_the_pause_is_a_cut():
    events = [_ev("end_of_turn", 800), _ev("injection_speech_end", 2000), _ev("end_of_turn", 3000)]
    assert clip_row(events, pad_s=0.0, text="")["cut"]
    twice = [_ev("injection_speech_end", 2000), _ev("end_of_turn", 2500), _ev("end_of_turn", 2900)]
    assert clip_row(twice, 0.0, "")["cut"]


def test_run_that_stopped_on_a_fragment_is_a_cut():
    events = [
        _ev("injection_speech_end", 2000),
        _ev("asr_final", 2100, text="Set a timer for", trigger="model"),
        _ev("end_of_turn", 2100),
    ]
    assert clip_row(events, 0.0, "Set a timer for [[slnc 900]] five minutes.")["cut"]
    assert not clip_row(events, 0.0, "Set a timer for")["cut"]


def test_withdrawn_calls_count_as_llm_calls():
    events = [
        _ev("eager_eot", 1000, text="set a timer"),
        _ev("eager_cancel", 1100),
        _ev("injection_speech_end", 2000),
        _ev("end_of_turn", 2500),
        _ev("llm_request", 2500),
    ]
    row = clip_row(events, pad_s=0.0, text="")
    assert (row["eager"], row["withdrawn"], row["llm_calls"]) == (1, 1, 2)


def test_summary_groups_by_setting_and_kind():
    base = {"eot_ms": 100, "reply_ms": None, "cut": False, "triggers": ["timeout"],
            "eager": 0, "withdrawn": 0, "llm_calls": 1, "force_ms": None, "cost_usd": 0.001}  # fmt: skip
    rows = [{**base, "eot_threshold": 0.5, "kind": "chat"}, {**base, "eot_threshold": 0.7, "kind": "pause"}]
    lines = summarise(rows, "eot_threshold")
    assert [(x["eot_threshold"], x["kind"]) for x in lines] == [(0.5, "chat"), (0.7, "pause")]
    assert lines[0]["no_reply"] == 1
    assert lines[0]["timeout_ends"] == 1


def test_force_round_trip_is_reported():
    events = [
        _ev("injection_speech_end", 2000),
        _ev("force_end_turn", 2300, sent=True),
        _ev("asr_final", 2550, text="done", trigger="manual"),
        _ev("end_of_turn", 2560),
    ]
    row = clip_row(events, 0.0, "done")
    assert (row["forced"], row["force_ms"]) == (1, 250)
