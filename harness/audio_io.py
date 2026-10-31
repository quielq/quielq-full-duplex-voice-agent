"""AudioIO adapter: the one place that knows about audio hardware.

Devices are selected by name substring from the profile, never by index, so a
renamed or re-plugged device is found again. Pipecat's local transport uses
PyAudio (PortAudio) on both the Mac and the Pi.
"""

from __future__ import annotations

from pipecat.transports.local.audio import LocalAudioTransport, LocalAudioTransportParams


class EchoRisk(RuntimeError):
    """A live run would play the agent through speakers its own mic can hear."""


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


def is_builtin_speaker(name: str) -> bool:
    lowered = name.lower()
    return "macbook" in lowered and "speakers" in lowered


def output_device_name(index: int | None) -> str:
    """Name of the output device at ``index``, or of the system default for ``None``."""
    import pyaudio

    pa = pyaudio.PyAudio()
    try:
        info = pa.get_default_output_device_info() if index is None else pa.get_device_info_by_index(index)
        return str(info["name"])
    finally:
        pa.terminate()


def check_echo_risk(output_name: str, *, allow_speakers: bool) -> None:
    """Refuse a live run on built-in speakers: Pipecat has no AEC, so the agent hears itself."""
    if is_builtin_speaker(output_name) and not allow_speakers:
        raise EchoRisk(
            f"output is {output_name!r}: the mic would hear the agent and it would interrupt "
            "itself. Use headphones, or --echo-cancel, or audio.allow_speakers for an echo test."
        )


def local_transport(
    audio_cfg: dict, audio_out_filter=None, canceller=None, on_event=None
) -> LocalAudioTransport:
    """``canceller`` replaces the default EchoCanceller when ``echo_cancel`` is on."""
    output_index = resolve_device(audio_cfg.get("output_device"), "output")
    echo_cancel = audio_cfg["echo_cancel"]
    check_echo_risk(
        output_device_name(output_index), allow_speakers=audio_cfg["allow_speakers"] or echo_cancel
    )
    audio_in_filter = None
    if echo_cancel:
        from harness.echo_cancel import EchoCanceller

        canceller = canceller or EchoCanceller(
            audio_cfg["in_sample_rate"],
            audio_cfg["out_sample_rate"],
            audio_cfg["echo_delay_ms"],
            on_event=on_event,
        )
        audio_in_filter = canceller.capture_filter()
        audio_out_filter = canceller.reference_filter(audio_out_filter)
    params = LocalAudioTransportParams(
        audio_in_filter=audio_in_filter,
        audio_out_filter=audio_out_filter,
        audio_in_enabled=True,
        audio_out_enabled=True,
        audio_in_sample_rate=audio_cfg["in_sample_rate"],
        audio_out_sample_rate=audio_cfg["out_sample_rate"],
        input_device_index=resolve_device(audio_cfg.get("input_device"), "input"),
        output_device_index=output_index,
    )
    return LocalAudioTransport(params)


if __name__ == "__main__":
    for d in list_devices():
        print(f"{d['index']:>3}  in={d['maxInputChannels']} out={d['maxOutputChannels']}  {d['name']}")
