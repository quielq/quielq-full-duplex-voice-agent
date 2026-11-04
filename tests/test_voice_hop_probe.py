"""bench/voice_hop_probe on a few frames: loopback fakes, dummy keys, no spend."""

from __future__ import annotations

import json
import os

from bench import voice_hop_probe as hop


async def test_probe_shape():
    before = {k: os.environ.get(k) for k in ("DEEPGRAM_API_KEY", "ME3_VOICE_LISTEN_UPSTREAM")}
    report = await hop.probe(frames=5, opens=1, transports=("stdio", "http"))
    assert {k: os.environ.get(k) for k in before} == before  # the environment is put back
    assert report["frames"] == 5 and set(report["results"]) == {"stdio", "http"}
    for res in report["results"].values():
        for metric in hop.METRICS:
            row = res[metric]
            for path in ("direct", "relay"):
                assert set(row[path]) == {"n", "p50", "p95", "max"}
                assert row[path]["n"] > 0 and 0 <= row[path]["p50"] <= row[path]["p95"] <= row[path]["max"]
            assert isinstance(row["added_p50_ms"], float)
        assert res["listen_up_ms"]["relay"]["n"] == 5 and res["speak_down_ms"]["relay"]["n"] == 5
        assert set(res["cold_open_ms"]) == {"listen", "speak"}
    assert hop.DUMMY not in json.dumps(report)
    assert "stdio" in hop.table(report)


def test_main_writes_json(tmp_path, monkeypatch, capsys):
    async def fake_probe(frames, opens, transports):
        return {"frames": frames, "machine": {"system": "x", "arch": "y"}, "results": {}}

    monkeypatch.setattr(hop, "probe", fake_probe)
    assert hop.main(["--frames", "3", "--out", str(tmp_path)]) == 0
    [path] = tmp_path.glob("voice-hop-*.json")
    assert json.loads(path.read_text())["frames"] == 3
    assert "3 frames" in capsys.readouterr().out
