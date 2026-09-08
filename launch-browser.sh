#!/bin/bash
# Launch Chrome or Brave with the flags browser-broker needs. Uses your REAL
# profile, so every login you already have carries into the broker's tabs.
#
# The --disable-*backgrounding* flags are not optional. Agent tabs are ordinary
# BACKGROUND tabs, and Chromium throttles those hard: timers stall and modern
# SPAs mount zero rows in a tab that is not in front. These flags keep a
# background tab painting exactly like the one you are looking at.
set -e
PORT="${BROKER_UPSTREAM_PORT:-9229}"   # the proxy owns 9222; the real browser hides behind it
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
