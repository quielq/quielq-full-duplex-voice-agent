# ME3 quick start on the Pi

Six steps. Steps 1 to 4 make no cloud calls and spend nothing. Stop at the first step that fails and keep its output.

You need a Raspberry Pi 5 with 64-bit Raspberry Pi OS (desktop), a USB mic, a USB speaker, the 7-inch display, and a network.

**Where it stands (Nov 6):** setup, tests, and a live run with Flux, Haiku 5.5, Cartesia, the face, and Spotify all worked on the Pi (Bookworm). On Nov 5 a live run through both voice servers passed, with the short echo probe, barge-in, and live `bash` in `bwrap` (run `20261105-203514-1a2f7a`), and the voice server hop probes passed. On Nov 6 the class benchmark ran on the Pi with every course row under the class target (`docs/class-benchmark.md`). Not yet done on the Pi: the long echo drift test (skipped), `serve` in `bwrap`, and the boot service.

The repo is public (since Nov 5), so the Pi pulls straight from GitHub. To update an existing checkout, run `git checkout main && git pull --ff-only origin main`, then `uv sync`.

## 1. Get the code and install

```bash
git clone https://github.com/quielq/quielq-full-duplex-voice-agent.git
```

```bash
cd quielq-full-duplex-voice-agent && scripts/pi_setup.sh
```

The script installs PortAudio, PipeWire ALSA, bubblewrap, uv, and Python 3.13, and creates `.env`. Add your keys to `.env` by hand. Never paste them into a chat or a log.

## 2. Check the machine

```bash
uv run pytest -q
```

```bash
uv run python -m bench.doctor --profile pi --offline
```

In the doctor's output, the `sandbox` section must say `bwrap`, and the `pi` section must show no throttling. Throttling means the power supply or cooling needs fixing first, because it stalls audio with no error.

## 3. Pick the audio devices

```bash
uv run python -m harness.audio_io
```

`configs/pi.yaml` uses the `pipewire` device for both, which worked on Nov 2. Change `audio.input_device` and `audio.output_device` only if it is missing. Point the speaker away from the mic.

If another voice program holds the mic (VCM on our Pi), stop it before a run.

## 4. Test the echo, with no cloud calls

Make the speech clips on the Mac, since `say` is Mac only:

```bash
uv run python -m bench.echo_probe --make-speech data/echo_speech
```

Copy `data/echo_speech` to the same place on the Pi. Then, on the Pi, stay quiet while this plays:

```bash
uv run python -m bench.echo_probe --profile pi --speech data/echo_speech --talker
```

It passes when no speech is detected on the cancelled audio after the first few seconds and both talkers are caught. Then run the long one, about 5 minutes through the real transport, which shows any clock drift between the two USB devices:

```bash
uv run python -m bench.echo_probe --profile pi --speech data/echo_speech --transport --repeat 10
```

Set `audio.echo_delay_ms` in `configs/pi.yaml` to the best delay it reports. If it fails, the fallback is PipeWire echo-cancel (ADR-029).

## 5. Run it live (paid)

```bash
ME3_WORKSPACE_LIVE=1 ME3_TOOL_STAGES=search=tavily,delegate=anthropic uv run python -m harness.bot --profile pi --stages stt=deepgram_flux,llm=anthropic,tts=cartesia --ui --spotify-player
```

`pi.yaml` already turns echo cancelling on, so no `--echo-cancel` is needed. `--spotify-player` plays music in the face page (needs Premium, ADR-031). Drop it if you do not need music.

The two switches in front work as on the Mac (`docs/quickstart-mac.md`, step 5): `ME3_WORKSPACE_LIVE=1` makes `bash`, `serve` and `open_in_browser` real, and `delegate=anthropic` runs background tasks on Haiku 5.5. Leave them out and those tools stay mocks, so a task reports done without making anything. `open_in_browser` opens on the Pi's own display.

To test the voice servers on the Pi, use `--stages stt=voice_listen,llm=anthropic,tts=voice_speak`, then check the hop cost for free:

```bash
uv run python -m bench.voice_hop_probe
```

Afterwards, read the run:

```bash
uv run python -m bench.run_report --live
```

If Cartesia never speaks and the log shows `timed out during opening handshake`, check for an IPv6 address on the Wi-Fi (`ip -6 addr show wlan0`). On Nov 6 the Wi-Fi started handing one out, the IPv6 path did not reach Cartesia, and the WebSocket tried it first. Turn IPv6 off until the next reboot with `sudo sysctl -w net.ipv6.conf.wlan0.disable_ipv6=1` (`docs/class-benchmark.md`, Pi run).

## 6. Start at boot

Turn on desktop autologin in `raspi-config`, then:

```bash
scripts/pi_setup.sh --service
```

The service starts on fakes, so a reboot spends nothing. To go live, edit `~/.config/me3/bot.env` as its comments say, then:

```bash
systemctl --user restart me3-bot
```

To watch it:

```bash
journalctl --user -u me3-bot -f
```

Messages and calls stay mocks on the Pi.
