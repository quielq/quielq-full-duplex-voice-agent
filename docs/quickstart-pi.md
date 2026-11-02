# ME3 quick start on the Pi

This is the order for the Pi port. None of it has run on a Pi yet, so a step may need a fix. Stop at the first step that fails and keep the output. Steps 1 to 4 make no cloud calls and spend nothing.

You need a Raspberry Pi 5 with 64-bit Raspberry Pi OS Trixie (the desktop version), the USB mic, the USB speaker, the 7-inch display, and a network connection.

## 1. Get the code and install

The repo is private. Add a read-only GitHub deploy key on the Pi, or copy the folder from the Mac. Then, on the Pi:

```bash
scripts/pi_setup.sh
```

Add the keys to `.env` by hand. Never paste them into a chat or a log.

## 2. Check the machine

```bash
uv run pytest -q
```

```bash
uv run python -m bench.doctor --profile pi --offline
```

The doctor's `models` section times Silero, Smart Turn, and echo cancelling. For comparison, the MacBook Air gave 0.18 ms per frame, 40 ms per decision, and 0.12 ms per 20 ms block. Its `pi` section shows temperature, memory, and throttling. Any throttling warning means the power supply or cooling needs attention first, because it stalls audio with no error. Its `sandbox` section must say `bwrap`.

## 3. Choose the audio devices

```bash
uv run python -m harness.audio_io
```

Check that a `pipewire` or `default` device appears, because the raw USB devices often show `16k in=no`. How PipeWire appears to PortAudio on this image is not yet known. Put name substrings in `audio.input_device` and `audio.output_device` in `configs/pi.yaml`. Keep the speaker away from the mic and aim it the other way.

## 4. Test the echo, with no cloud calls

`say` is Mac-only, so make the clips on the Mac:

```bash
uv run python -m bench.echo_probe --make-speech data/echo_speech
```

Copy `data/echo_speech` to the same place on the Pi. `data/` is not in git. On the Pi, stay quiet during these. The first run plays about 30 s of speech, and the last about 5 minutes, to expose drift between the two USB clocks.

```bash
uv run python -m bench.echo_probe --profile pi --speech data/echo_speech
```

```bash
uv run python -m bench.echo_probe --profile pi --speech data/echo_speech --talker
```

```bash
uv run python -m bench.echo_probe --profile pi --speech data/echo_speech --transport --repeat 10
```

Each prints a JSON report. Passing means no onsets on the cancelled audio after the first few seconds, and in the `--talker` run, both talkers caught. Set `audio.echo_delay_ms` to the best delay. If this fails, the next step is PipeWire echo-cancel (ADR-029).

## 5. First live run (spends, needs approval)

```bash
uv run python -m harness.bot --profile pi --stages stt=deepgram_flux,llm=anthropic,tts=cartesia --ui
```

Then read the run with `uv run python -m bench.run_report --live`. The test: 60 s of bot speech with nobody talking gives no `user_turn_start`, and a real interruption works. Live runs log a `system_stats` event every 5 s to the event log.

## 6. Start at boot

Check that the desktop runs labwc, and turn on desktop autologin in `raspi-config`. Then:

```bash
scripts/pi_setup.sh --service
```

The service starts on fakes, so a reboot spends nothing. To go live, edit `~/.config/me3/bot.env` as its comments say (ADR-030), then restart it:

```bash
systemctl --user restart me3-bot
```

To see what it is doing:

```bash
journalctl --user -u me3-bot -f
```

For music in the face, add `--spotify-player` (ADR-031). It needs full Premium, the face open in Chromium, and one new `uv run python -m bench.spotify_login` on the Mac, then `.env` copied over again. Messages and calls stay mocks on the Pi.
