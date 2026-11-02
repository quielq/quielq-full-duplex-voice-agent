#!/usr/bin/env bash
# Show the robot face full screen (ADR-027, ADR-030). Started from ~/.config/labwc/autostart.
set -u

url="http://127.0.0.1:8770"
browser="$(command -v chromium || command -v chromium-browser || true)"
if [[ -z "$browser" ]]; then
  echo "no chromium on this machine" >&2
  exit 1
fi

until curl -fs -o /dev/null "$url"; do sleep 1; done

while true; do
  "$browser" "$url" --kiosk --noerrdialogs --disable-infobars --no-first-run \
    --password-store=basic --ozone-platform=wayland --autoplay-policy=no-user-gesture-required
  sleep 2
done
