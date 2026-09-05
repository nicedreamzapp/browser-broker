#!/bin/bash
# Install browser-broker as a macOS LaunchAgent that starts at login and
# restarts itself if it dies. Run from the repo directory.
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
PLIST="$HOME/Library/LaunchAgents/com.browser-broker.plist"
python3 -c "import websocket" 2>/dev/null || pip3 install websocket-client
sed "s|__BROKER_DIR__|$DIR|g" "$DIR/launchd/com.browser-broker.plist" > "$PLIST"
launchctl bootout "gui/$(id -u)/com.browser-broker" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
sleep 3
curl -sf -m 5 http://127.0.0.1:${BROKER_PORT:-9223}/health && echo && echo "browser-broker installed and running" \
  || { echo "broker did not answer — is the browser up? try ./launch-browser.sh" >&2; exit 1; }
