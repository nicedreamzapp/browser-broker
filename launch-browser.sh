#!/bin/bash
# Launch Chrome or Brave with the flags browser-broker needs. Uses your REAL
# profile, so every login you already have carries into the broker's tabs.
#
# The two --disable-*backgrounding* flags are not optional: they are what make
# an off-screen window keep painting. Without them Chromium throttles occluded
# windows and modern SPAs mount zero rows in the hidden tab.
set -e
PORT="${BROKER_CDP_PORT:-9222}"
APP="${BROWSER_APP:-Brave Browser}"   # or "Google Chrome"

if curl -sf -m 3 "http://127.0.0.1:$PORT/json/version" >/dev/null 2>&1; then
  echo "browser already answering CDP on $PORT"; exit 0
fi
osascript -e "tell application \"$APP\" to quit" >/dev/null 2>&1 || true
sleep 3
open -a "$APP" --args \
  "--remote-debugging-port=$PORT" "--remote-allow-origins=*" --restore-last-session \
  --disable-backgrounding-occluded-windows --disable-renderer-backgrounding \
  --disable-background-timer-throttling
sleep 6
curl -sf -m 3 "http://127.0.0.1:$PORT/json/version" >/dev/null && echo "browser up with CDP on $PORT" \
  || { echo "browser did not come up on $PORT" >&2; exit 1; }
