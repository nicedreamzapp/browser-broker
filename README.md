<div align="center">

# 🚦 browser-broker

### Your second agent just stole the tab your first one was using.

**One logged-in browser · many agents · nobody fights, and nobody touches your tabs**
**~300 lines · one dependency · it *is* port 9222, so no tool changes a single line**

[![License: MIT](https://img.shields.io/badge/license-MIT-6366f1.svg?style=for-the-badge)](LICENSE)
[![Python 3.9+](https://img.shields.io/badge/python-3.9+-22c55e.svg?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org/)
[![Zero tool changes](https://img.shields.io/badge/tool_changes-zero-blue?style=for-the-badge)](#it-is-port-9222)
[![Self-hosted](https://img.shields.io/badge/local-only-22c55e?style=for-the-badge)](#security)

</div>

---

## 🛑 Two agents, one browser?

If you just ran a second browser agent and watched it **navigate a tab out from under the first one** — or reach for "the active tab" and grab the page *you* were reading — that is what this fixes.

```bash
curl -fsSL https://raw.githubusercontent.com/nicedreamzapp/browser-broker/main/get.sh | bash
```

Nothing else changes. Your tools keep dialing port 9222 exactly as they do today.

The Chrome DevTools Protocol has **no concept of ownership**. Every agent sees every tab, including yours, and none of them can tell that another one is already working in the tab it just grabbed. The usual advice is "run them on different ports," which means a different browser, which means losing the logins that made your real browser worth driving in the first place.

browser-broker adds the missing layer: a process that owns the debug port and hands each agent an **exclusive lease** on its own tab.

---

## <a name="it-is-port-9222"></a>🔌 It *is* port 9222

`proxy.py` takes 9222. Your real browser moves to 9229 behind it. Every tool keeps its existing command line and gets a browser in which **the only tabs that exist are the ones it was given**:

```
agent-browser --cdp 9222           ┐
chrome-devtools-mcp --browser-url  ├──▶  proxy :9222  ──▶  your browser :9229
Playwright connectOverCDP          │     ownership +        real profile,
curl /json → first page            │     focus block        real logins
raw websockets, browser-use …      ┘
```

- `Target.getTargets`, `setDiscoverTargets`, `attachToTarget` and auto-attach events are all filtered to the caller's own tabs. Your tabs and other agents' tabs are not merely off-limits — **they do not exist** from where that client stands.
- `Target.activateTarget`, `Page.bringToFront` and `/json/activate` are answered with success and **never forwarded**. Nothing an agent does can put itself in front of you.
- A legacy client that does `GET /json` and grabs the first page gets its own fresh tab, because that is the only page in its list.
- A browser-level websocket is one client for as long as it stays connected; its tabs go away when it does.

Verified with Playwright's `connect_over_cdp`, a raw first-page websocket client, two concurrent browser-level clients, and the REST lease API — Gmail, LinkedIn and Yahoo Mail all loaded logged in, with the human's tabs untouched throughout.

---

## 🐍 Or use it directly

```python
from broker_client import lease

with lease("my-agent", url="https://mail.google.com", purpose="inbox sweep") as tab:
    tab.eval("document.title")   # you, alone, in a tab nobody else can touch
```

The lease auto-renews while you work and releases on exit, including on an exception. A crashed agent frees its tab instead of stranding it.

---

## 🧠 Why it works

**Agent tabs are ordinary background tabs.** In your window, behind what you are looking at. No second window, nothing parked off-screen, nothing headless — headless can't use the profile that has your logins, and a stray window just shoves yours aside. Isolation was never the window's job: the ownership registry and the swallowed focus commands do all of it.

**The rule is deliberately dumb.** Agents only ever get tabs the broker opened. Anything *you* opened is invisible to every agent, permanently, with no heuristics. Focus detection was rejected on purpose — window checks false-negative across macOS Spaces, and a heuristic that is wrong once is worse than a rule that is boring. `/adopt` exists for deliberately driving an existing tab; you have to ask for it by name.

**Leases expire.** 300 s by default, auto-renewed every 60 s by the client, reaped every 5 s. No deadlocks.

**It reconnects.** Browsers auto-update, crash, get quit. Every CDP call retries once on a fresh connection.

**It has a heartbeat.** One log line an hour. A silent daemon is indistinguishable from a dead one.

---

## 📡 API

| | |
|---|---|
| `POST /lease` | `{owner, url, purpose, ttl}` → `{lease_id, target_id, ws_url, expires_in}` |
| `POST /renew` | `{lease_id, ttl}` |
| `POST /release` | `{lease_id, close}` — closes the tab unless `close: false` |
| `POST /adopt` | `{owner, target_id, purpose, ttl}` — take over an existing tab, explicitly |
| `GET /status` | who holds what, for how long, and whether the browser is alive |
| `GET /health` | |

`ws_url` is a normal CDP page socket — drive it with the bundled client, Playwright's `connect_over_cdp`, or raw websockets. The broker doesn't proxy your traffic; it decides who is allowed in.

Everything is configured by environment: `BROKER_PROXY_PORT` (9222, what tools dial), `BROKER_UPSTREAM_PORT` (9229, your real browser), `BROKER_PORT` (9223, lease API), `BROKER_TTL`, `BROKER_HEARTBEAT`, `BROKER_LOG`, and `BROKER_DEBUG=1` for a handshake trace.

---

## 🚀 Install

```bash
curl -fsSL https://raw.githubusercontent.com/nicedreamzapp/browser-broker/main/get.sh | bash
```

Or by hand:

```bash
git clone https://github.com/nicedreamzapp/browser-broker && cd browser-broker
./launch-browser.sh   # your real profile, CDP on :9229
./install.sh          # LaunchAgent for proxy.py: :9222 for tools, :9223 lease API
curl localhost:9223/status
```

Already have something on 9222 you can't restart? Try it on another port first:
`BROKER_PROXY_PORT=9224 BROKER_UPSTREAM_PORT=9222 BROKER_PORT=9225 python3 proxy.py`

Python 3.9+ and `websocket-client`, which is the only dependency. Chrome as well as Brave: `BROWSER_APP="Google Chrome" ./launch-browser.sh`.

**Windows / Linux:** `proxy.py`, `broker.py` and `broker_client.py` are pure Python and don't care about the OS — only the two shell scripts are macOS. Launch your browser with `--remote-debugging-port=9229 --remote-allow-origins=* --disable-backgrounding-occluded-windows --disable-renderer-backgrounding`, then run `python proxy.py` from a startup task. Those backgrounding flags matter: agent tabs are background tabs, and a throttled background tab mounts zero rows in a modern SPA. Tested on macOS only so far — reports welcome.

---

## 🔒 Security

Localhost only, no auth. Anything that can reach `127.0.0.1:9223` can drive your logged-in browser — which is already true of `127.0.0.1:9222` the moment you enable remote debugging. Don't expose either.

---

## 📊 Status

Built in one night and proven with four tests: a leased tab loading Gmail authenticated while the human's visible tab sat on a different account; two agents running concurrently on separate targets; a simulated agent crash whose lease expired and was reclaimed; the human's tabs untouched throughout. Two production agents — a Yahoo inbox sweeper and a LinkedIn notification reaper — run on it daily, with a fallback to raw CDP if the broker is down.

Not yet built: a lane router that skips the browser entirely when a job is really an API call, and per-domain serialization so two agents never hammer one site as the same user.

---

## 🧩 The local-first stack

One piece of a set that runs entirely on your own machine, no cloud:

| Project | Role | What it does |
|---|---|---|
| 🧠 [claude-code-local](https://github.com/nicedreamzapp/claude-code-local) | Brain | Claude Code on a local MLX model — the inference the agents think with |
| 🌐 [browser-agent](https://github.com/nicedreamzapp/browser-agent) | Hands | Drives your real browser via CDP — iframes, Shadow DOM, ProseMirror |
| 🚦 **browser-broker** | Traffic cop | Leases each agent its own tab so hands never collide, or grab yours |

---

## 🤝 Prior art and neighbours

This problem is being attacked from several directions right now. These aren't competitors so much as pieces of the same puzzle, and browser-broker borrows from the ideas in them:

- [vercel-labs/agent-browser](https://github.com/vercel-labs/agent-browser) — `--cdp` mode with `--pin-tab` binds a session to one tab; [issue #1068](https://github.com/vercel-labs/agent-browser/issues/1068) asks for `Target.createBrowserContext` so parallel sessions get isolated cookies.
- [leeguooooo/chrome-use](https://github.com/leeguooooo/chrome-use) — per-session coloured tab groups in your real Chrome, never force-fronts a tab.
- [mediar-ai/playwright-mcp-orchestrator](https://github.com/mediar-ai/playwright-mcp-orchestrator) — one Chrome, many `@playwright/mcp` sessions, each scoped to its own tabs.
- [henu-wang/chrome-mcp-proxy](https://github.com/henu-wang/chrome-mcp-proxy) and `chrome-devtools-mcp-mux` — proxies in front of `chrome-devtools-mcp` that block focus-stealing and give each client its own tabs.
- [captivus/chrome-agent](https://github.com/captivus/chrome-agent), [pasky/chrome-cdp-skill](https://github.com/pasky/chrome-cdp-skill), [ofershap/real-browser-mcp](https://github.com/ofershap/real-browser-mcp) — drive your real, logged-in browser over CDP or an extension.

What browser-broker adds is a layer that sits *below* all of them. It doesn't care which tool the agent is — a CLI, an MCP server, Playwright, raw websockets — because it hands out plain CDP page sockets. The ownership rule and the transparent 9222 endpoint are the parts that don't exist elsewhere yet. If you maintain one of the projects above and want a lease layer under yours, open an issue.

---

## 📜 License

MIT — see [LICENSE](LICENSE). Built on other people's work — see [CREDITS.md](CREDITS.md).
