# browser-broker

**One logged-in browser. Many agents. Nobody fights, and nobody touches the human's tabs.**

Every local browser agent drives the same Chrome or Brave over the DevTools Protocol, and CDP has no idea of ownership. The moment you run a second agent, two of them grab the same tab, one navigates it out from under the other, and neither can tell. Worse: an agent that reaches for "the active tab" gets whatever *you* just clicked on.

browser-broker is a ~300-line process that owns the debug port and hands agents **exclusive leases** on tabs. Work tabs live in a real window parked off-screen — it renders normally, you never see it, and your logins carry over because it's your actual profile.

```python
from broker_client import lease

with lease("my-agent", url="https://mail.google.com", purpose="inbox sweep") as tab:
    tab.eval("document.title")   # you, alone, in a tab nobody else can touch
```

The lease auto-renews while you work and releases on exit — including on an exception. A crashed agent frees its tab instead of stranding it.

## Why it works

**The rule is deliberately dumb.** The broker only hands out tabs the broker opened. Anything the human opened is invisible to every agent, permanently, with no heuristics. Focus-detection was rejected on purpose — window checks false-negative across macOS Spaces, and a heuristic that's wrong once is worse than a rule that's boring. `/adopt` exists for deliberately driving an existing tab; you have to ask for it by name.

**Hidden ≠ headless.** Chromium takes an exclusive lock on the profile directory, so you cannot run a second, headless browser on your real logins. But a window positioned off-screen paints exactly like a visible one *as long as backgrounding is disabled* — that's what `launch-browser.sh` does. You get headless-style invisibility with your real cookies. Verified on Gmail, WordPress and Yahoo Mail's virtualized list, which mounts zero rows in a throttled window.

**Leases expire.** Default 300 s, auto-renewed every 60 s by the client, reaped every 5 s. No deadlocks.

**It reconnects.** Browsers auto-update, crash, get quit. Every CDP call retries once on a fresh connection.

**It has a heartbeat.** One log line an hour. A silent daemon is indistinguishable from a dead one.

## Install

```bash
git clone https://github.com/nicedreamzapp/browser-broker
cd browser-broker
./launch-browser.sh        # your real profile, with the flags the hidden window needs
./install.sh               # LaunchAgent: starts at login, restarts if it dies
curl localhost:9223/status
```

Requires Python 3.9+ and `websocket-client` (the only dependency). Chrome works too: `BROWSER_APP="Google Chrome" ./launch-browser.sh`.

**Windows / Linux:** `broker.py` and `broker_client.py` are pure Python and don't care about the OS — only the two shell scripts are macOS. Launch Chrome or Brave yourself with `--remote-debugging-port=9222 --remote-allow-origins=* --disable-backgrounding-occluded-windows --disable-renderer-backgrounding`, then run `python broker.py` from a startup task. The off-screen window uses `Browser.setWindowBounds`, which is standard CDP. Tested on macOS only so far — reports welcome.

## API

| | |
|---|---|
| `POST /lease` | `{owner, url, purpose, hidden, ttl}` → `{lease_id, target_id, ws_url, expires_in}` |
| `POST /renew` | `{lease_id, ttl}` |
| `POST /release` | `{lease_id, close}` — closes the tab unless `close: false` |
| `POST /adopt` | `{owner, target_id, purpose, ttl}` — take over an existing tab, explicitly |
| `GET /status` | who holds what, for how long, and whether the browser is alive |
| `GET /health` | |

`ws_url` is a normal CDP page socket — drive it with the bundled client, Playwright's `connect_over_cdp`, or raw websockets. The broker doesn't proxy traffic; it just decides who's allowed in.

Configuration is all environment: `BROKER_PORT` (9223), `BROKER_CDP_PORT` (9222), `BROKER_TTL`, `BROKER_HEARTBEAT`, `BROKER_HIDDEN_LEFT`, `BROKER_LOG`.

## Security

Localhost only, no auth. Anything that can reach `127.0.0.1:9223` can drive your logged-in browser — which is already true of `127.0.0.1:9222` the moment you enable remote debugging. Don't expose either.

## Status

Built in one night on a Mac mini and proven with four tests: a hidden tab loading Gmail authenticated while the human's visible tab sat on a different account; two agents running concurrently on separate targets; a simulated agent crash whose lease expired and was reclaimed; the human's tab untouched throughout. Two production agents (a Yahoo inbox sweeper and a LinkedIn notification reaper) have been moved onto it, with a fallback to raw CDP if the broker is down. Not yet built: a lane router that skips the browser entirely when a job is really an API call, and per-domain serialization so two agents never hammer one site as the same user.

Sibling project: [browser-agent](https://github.com/nicedreamzapp/browser-agent), the local MLX agent this was built to keep out of the human's tabs.

## License

MIT — see [LICENSE](LICENSE). Built on other people's work — see [CREDITS.md](CREDITS.md).
