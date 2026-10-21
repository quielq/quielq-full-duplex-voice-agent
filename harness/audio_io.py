"""AudioIO adapter: the one place that knows about audio hardware.

Devices are selected by name substring from the profile, never by index, so a
renamed or re-plugged device is found again. Pipecat's local transport uses
PyAudio (PortAudio) on both the Mac and the Pi.
"""

from __future__ import annotations

from pipecat.transports.local.audio import LocalAudioTransport, LocalAudioTransportParams


def list_devices() -> list[dict]:
    import pyaudio

    pa = pyaudio.PyAudio()
    try:
        return [pa.get_device_info_by_index(i) for i in range(pa.get_device_count())]
    finally:
        pa.terminate()


def resolve_device(name: str | None, kind: str) -> int | None:
    """Return the PyAudio index of the first device whose name contains ``name``.

    ``kind`` is "input" or "output". ``None`` means the system default.
    """
    if not name:
        return None
    channels_key = "maxInputChannels" if kind == "input" else "maxOutputChannels"
    for d in list_devices():
        if name.lower() in d["name"].lower() and d[channels_key] > 0:
            return int(d["index"])
    raise RuntimeError(f"no {kind} audio device matching {name!r}; run python -m harness.audio_io")


def local_transport(audio_cfg: dict, audio_out_filter=None) -> LocalAudioTransport:
    params = LocalAudioTransportParams(
        audio_out_filter=audio_out_filter,
        audio_in_enabled=True,
        audio_out_enabled=True,
        audio_in_sample_rate=audio_cfg["in_sample_rate"],
        audio_out_sample_rate=audio_cfg["out_sample_rate"],
        input_device_index=resolve_device(audio_cfg.get("input_device"), "input"),
        output_device_index=resolve_device(audio_cfg.get("output_device"), "output"),
    )
    return LocalAudioTransport(params)


if __name__ == "__main__":
    for d in list_devices():
        print(f"{d['index']:>3}  in={d['maxInputChannels']} out={d['maxOutputChannels']}  {d['name']}")
