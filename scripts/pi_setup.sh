#!/usr/bin/env bash
# Phase 2 Pi setup. Safe to run twice. Spends nothing and never edits an existing .env.
#
#   scripts/pi_setup.sh             # system packages, uv, Python 3.13, dependencies
#   scripts/pi_setup.sh --service   # also the me3-bot service and the kiosk (ADR-030)
set -euo pipefail

repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
service=false
case "${1:-}" in
  "") ;;
  --service) service=true ;;
  *) echo "usage: scripts/pi_setup.sh [--service]" >&2; exit 2 ;;
esac

if [[ "$(uname -s)" != Linux || "$(uname -m)" != aarch64 ]]; then
  echo "this is for a 64-bit Raspberry Pi OS, not $(uname -s) $(uname -m)" >&2
  exit 1
fi

# PyAudio has no Linux wheel, so it compiles here against PortAudio.
sudo apt-get update
sudo apt-get install -y build-essential pkg-config portaudio19-dev pipewire-alsa \
  bubblewrap xdg-utils curl

if ! command -v uv >/dev/null && [[ ! -x "$HOME/.local/bin/uv" ]]; then
  curl -LsSf https://astral.sh/uv/install.sh | sh
fi
export PATH="$HOME/.local/bin:$PATH"

cd "$repo"
uv python install 3.13
uv sync --locked

if [[ ! -e .env ]]; then
  cp .env.example .env
  chmod 600 .env
  echo "created .env: add the keys by hand"
fi

# Full gain made the bot hear itself on Nov 2. PipeWire keeps this across reboots.
if command -v wpctl >/dev/null; then
  wpctl set-volume @DEFAULT_AUDIO_SOURCE@ 0.5 || echo "could not set the mic gain: set it to 50% by hand"
fi

if [[ "$service" == true ]]; then
  units="$HOME/.config/systemd/user"
  mkdir -p "$units" "$HOME/.config/me3" "$HOME/.config/labwc"
  sed "s|@REPO@|$repo|g; s|@UV@|$(command -v uv)|g" scripts/systemd/me3-bot.service > "$units/me3-bot.service"
  if [[ ! -e "$HOME/.config/me3/bot.env" ]]; then
    cp scripts/systemd/bot.env.example "$HOME/.config/me3/bot.env"
    chmod 600 "$HOME/.config/me3/bot.env"
  fi
  kiosk="$repo/scripts/pi_kiosk.sh &"
  touch "$HOME/.config/labwc/autostart"
  grep -qxF "$kiosk" "$HOME/.config/labwc/autostart" || echo "$kiosk" >> "$HOME/.config/labwc/autostart"
  sudo loginctl enable-linger "$USER"
  systemctl --user daemon-reload
  systemctl --user enable me3-bot.service
  echo "enabled me3-bot on fakes. To go live, edit ~/.config/me3/bot.env"
fi

uv run python -m bench.doctor --profile pi --offline --quick || true
