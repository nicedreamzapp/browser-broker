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

## Transparent mode — zero changes to any tool

`proxy.py` *is* port 9222. The real browser runs on 9229 behind it. Every tool keeps dialing 9222 exactly as before and gets a browser in which the only tabs that exist are the ones it was given:

```
agent-browser --cdp 9222          ┐
chrome-devtools-mcp --browser-url ├──▶  proxy :9222  ──▶  real browser :9229
Playwright connectOverCDP         │     (ownership,        (your profile,
curl /json → first page           │      focus-block,       your logins)
raw websocket, browser-use …      ┘      off-screen park)
```

- A browser-level websocket is one client for as long as it stays connected; its tabs vanish when it goes away.
- A legacy client that does `GET /json` and grabs the first page grabs its own fresh hidden tab — that is the only page in the list.
- `Target.activateTarget`, `Page.bringToFront` and `/json/activate` are answered with success and never forwarded. Nothing an agent does can put it in front of you.
- `Target.getTargets`, `setDiscoverTargets`, `attachToTarget`, auto-attach events: all filtered to the client's own targets. Your tabs and other agents' tabs are not merely off-limits, they do not exist from where the client stands.

Verified with Playwright's `connect_over_cdp`, a raw "first page" websocket client, two concurrent browser-level clients, and the REST lease API — Gmail, LinkedIn and Yahoo Mail all loaded logged-in, in hidden windows, with the human's tabs untouched throughout.

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
./launch-browser.sh        # your real profile on :9229, with the flags the hidden window needs
./install.sh               # LaunchAgent for proxy.py: :9222 for tools, :9223 lease API
curl localhost:9223/status
```

Already have a browser on 9222 that you can't restart right now? Run the proxy on another port for a look: `BROKER_PROXY_PORT=9224 BROKER_UPSTREAM_PORT=9222 BROKER_PORT=9225 python3 proxy.py`.

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

Configuration is all environment: `BROKER_PROXY_PORT` (9222, what tools dial), `BROKER_UPSTREAM_PORT` (9229, the real browser), `BROKER_PORT` (9223, lease API), `BROKER_TTL`, `BROKER_HEARTBEAT`, `BROKER_HIDDEN_LEFT`, `BROKER_LOG`, `BROKER_DEBUG=1` for a handshake trace. `broker.py` is the earlier REST-only version and still runs standalone against a browser on 9222 (`BROKER_CDP_PORT`).

## Security

Localhost only, no auth. Anything that can reach `127.0.0.1:9223` can drive your logged-in browser — which is already true of `127.0.0.1:9222` the moment you enable remote debugging. Don't expose either.

## Status

Built in one night on a Mac mini and proven with four tests: a hidden tab loading Gmail authenticated while the human's visible tab sat on a different account; two agents running concurrently on separate targets; a simulated agent crash whose lease expired and was reclaimed; the human's tab untouched throughout. Two production agents (a Yahoo inbox sweeper and a LinkedIn notification reaper) have been moved onto it, with a fallback to raw CDP if the broker is down. Not yet built: a lane router that skips the browser entirely when a job is really an API call, and per-domain serialization so two agents never hammer one site as the same user.

## The local-first stack

This is one piece of a set that runs entirely on your Mac, no cloud:

| Project | Role | What it does |
|---|---|---|
| 🧠 [claude-code-local](https://github.com/nicedreamzapp/claude-code-local) | Brain | Claude Code on a local MLX model — the inference the agents think with |
| 🌐 [browser-agent](https://github.com/nicedreamzapp/browser-agent) | Hands | Drives your real browser via CDP — iframes, Shadow DOM, ProseMirror |
| 🚦 **browser-broker** | Traffic cop | Leases each agent its own hidden tab so hands never collide, or grab yours |

Brain on `:4000`, browser on `:9222`, broker on `:9223` deciding who gets which tab.

## Prior art and neighbours

This problem is being attacked from several directions right now. None of these are competitors so much as pieces of the same puzzle, and browser-broker borrows from the ideas in them:

- [vercel-labs/agent-browser](https://github.com/vercel-labs/agent-browser) — `--cdp` mode with `--pin-tab` binds a session to one tab; [issue #1068](https://github.com/vercel-labs/agent-browser/issues/1068) asks for `Target.createBrowserContext` so parallel sessions get isolated cookies.
- [leeguooooo/chrome-use](https://github.com/leeguooooo/chrome-use) — per-session coloured tab groups in your real Chrome, never force-fronts a tab.
- [mediar-ai/playwright-mcp-orchestrator](https://github.com/mediar-ai/playwright-mcp-orchestrator) — one Chrome, many `@playwright/mcp` sessions, each scoped to its own tabs.
- [henu-wang/chrome-mcp-proxy](https://github.com/henu-wang/chrome-mcp-proxy) and `chrome-devtools-mcp-mux` — proxies in front of `chrome-devtools-mcp` that block focus-stealing and give each client its own tabs.
- [captivus/chrome-agent](https://github.com/captivus/chrome-agent), [pasky/chrome-cdp-skill](https://github.com/pasky/chrome-cdp-skill), [ofershap/real-browser-mcp](https://github.com/ofershap/real-browser-mcp) — drive your real, logged-in browser over CDP or an extension.

What browser-broker adds is a layer that sits *below* all of them: it doesn't care which tool the agent is — a CLI, an MCP server, Playwright, raw websockets — because it hands out plain CDP page sockets. The ownership rule (agents only ever see tabs the broker opened) and the off-screen rendering window are the parts that don't exist elsewhere yet. If you maintain one of the projects above and want a lease layer under yours, open an issue.

## License

MIT — see [LICENSE](LICENSE). Built on other people's work — see [CREDITS.md](CREDITS.md).
