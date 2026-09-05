#!/usr/bin/env python3
"""browser-broker proxy — a transparent Chrome DevTools Protocol endpoint that
enforces tab ownership for EVERY client, with zero changes to any of them.

The real browser runs on an upstream port (default 9229). This process
listens on the port every tool already expects (default 9222) and speaks
CDP back at them:

    agent-browser --cdp 9222, chrome-devtools-mcp --browser-url, Playwright's
    connectOverCDP, Puppeteer.connect, browser-use, a curl to /json, or a raw
    websocket — all unchanged.

What each of them gets is a browser in which the ONLY tabs that exist are the
ones this client was given. A tool that "grabs the first page" grabs its own
leased page, because that is the only page it can see. The human's tabs, and
every other agent's tabs, are simply not there.

Identity comes for free from the connection:
  * a browser-level websocket (/devtools/browser/...) is one client for as
    long as it stays connected; its tabs are released when it goes away.
  * a legacy HTTP client (GET /json, PUT /json/new, then a page websocket)
    claims a tab by attaching to it; the tab is released when the page socket
    closes.
  * the REST lease API on 9223 (broker_client.py) still works and is the way
    to hold a tab across reconnects, with a TTL.

Focus-stealing commands (Target.activateTarget, Page.bringToFront,
/json/activate) are answered with success and never forwarded, so no agent
can bring itself in front of the human. Agent tabs live in their own windows
parked off-screen — real windows that render normally because the browser is
launched with backgrounding disabled (launch-browser.sh).

Ownership rule, deliberately dumb: this proxy can only hand out tabs it
created. It never enumerates, attaches to, or closes anything else.
"""
import asyncio, json, os, sys, time, uuid, threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs, unquote

import websockets, logging
if os.environ.get("BROKER_DEBUG"):
    logging.basicConfig(level=logging.DEBUG, format="%(name)s %(message)s")
from websockets.asyncio.client import connect as ws_connect
from websockets.asyncio.server import serve as ws_serve
from websockets.http11 import Response
from websockets.datastructures import Headers

UPSTREAM = int(os.environ.get("BROKER_UPSTREAM_PORT", 9229))   # the real browser
LISTEN = int(os.environ.get("BROKER_PROXY_PORT", 9222))          # what tools dial
API = int(os.environ.get("BROKER_PORT", 9223))                   # REST lease API
DEFAULT_TTL = int(os.environ.get("BROKER_TTL", 300))
UNATTACHED_TTL = 60          # a tab handed to a legacy client that never attached
HEARTBEAT_EVERY = int(os.environ.get("BROKER_HEARTBEAT", 3600))
HIDDEN_BOUNDS = {"left": int(os.environ.get("BROKER_HIDDEN_LEFT", -4200)), "top": 0,
                 "width": 1440, "height": 900}
LOG = os.path.expanduser(os.environ.get("BROKER_LOG",
      os.path.join(os.path.dirname(os.path.abspath(__file__)), "broker.log")))
FOCUS_STEALERS = {"Target.activateTarget", "Page.bringToFront"}


def log(msg):
    line = f"[{datetime.now().isoformat(timespec='seconds')}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG, "a") as f:
            f.write(line + "\n")
    except OSError:
        pass


# ---------------------------------------------------------------------------
# upstream browser connection (the proxy's own control channel)
# ---------------------------------------------------------------------------
class Upstream:
    def __init__(self):
        self.ws = None
        self._id = 0
        self._pending = {}
        self._lock = asyncio.Lock()
        self.version = {}

    async def _http(self, path):
        import urllib.request
        def get():
            with urllib.request.urlopen(f"http://127.0.0.1:{UPSTREAM}{path}", timeout=10) as r:
                return json.load(r)
        return await asyncio.to_thread(get)

    async def connect(self):
        self.version = await self._http("/json/version")
        self.ws = await ws_connect(self.version["webSocketDebuggerUrl"], max_size=None)
        asyncio.create_task(self._reader())

    async def _reader(self):
        try:
            async for raw in self.ws:
                m = json.loads(raw)
                fut = self._pending.pop(m.get("id"), None)
                if fut and not fut.done():
                    fut.set_result(m)
        except Exception as e:
            log(f"upstream control socket dropped: {e}")
        self.ws = None

    async def send(self, method, **params):
        async with self._lock:
            if self.ws is None:
                await self.connect()
        self._id += 1
        fut = asyncio.get_running_loop().create_future()
        self._pending[self._id] = fut
        await self.ws.send(json.dumps({"id": self._id, "method": method, "params": params}))
        m = await asyncio.wait_for(fut, 20)
        if "error" in m:
            raise RuntimeError(m["error"].get("message"))
        return m.get("result", {})

    async def alive(self):
        try:
            await self.send("Browser.getVersion")
            return True
        except Exception:
            return False


# ---------------------------------------------------------------------------
# registry: which targets we made, who holds them
# ---------------------------------------------------------------------------
class Registry:
    def __init__(self, up):
        self.up = up
        self.tabs = {}      # target_id -> dict(owner, kind, hidden, created, expires, attached, lease_id)
        self.loop = None

    async def create(self, owner, url, hidden=True, kind="rest", ttl=None):
        # Always a fresh window: a plain new tab lands in the most recently
        # active window, which after the human clicks is THEIRS.
        res = await self.up.send("Target.createTarget", url=url or "about:blank",
                                 newWindow=True, background=True)
        tid = res["targetId"]
        # Register ownership BEFORE parking the window: Chrome tells auto-attached
        # clients about the new target immediately, and their filter must be
        # able to see that it is theirs.
        self.tabs[tid] = {"owner": owner, "kind": kind, "hidden": hidden,
                          "created": time.time(), "attached": 0,
                          "expires": time.time() + (ttl or UNATTACHED_TTL),
                          "lease_id": uuid.uuid4().hex[:12] if kind == "rest" else None}
        if hidden:
            try:
                win = await self.up.send("Browser.getWindowForTarget", targetId=tid)
                await self.up.send("Browser.setWindowBounds", windowId=win["windowId"],
                                   bounds={"windowState": "normal"})
                await self.up.send("Browser.setWindowBounds", windowId=win["windowId"],
                                   bounds=HIDDEN_BOUNDS)
            except Exception as e:
                log(f"could not park window: {e}")
        log(f"tab {tid[:12]} -> {owner} ({kind}{', hidden' if hidden else ''})")
        return tid

    def owned_by(self, owner):
        return [t for t, i in self.tabs.items() if i["owner"] == owner]

    def free_pool(self):
        """Tabs made for legacy /json clients that nobody has attached to yet."""
        return [t for t, i in self.tabs.items() if i["kind"] == "pool" and i["attached"] == 0]

    async def close(self, tid, why=""):
        info = self.tabs.pop(tid, None)
        if info is None:
            return
        try:
            await self.up.send("Target.closeTarget", targetId=tid)
        except Exception:
            pass
        log(f"tab {tid[:12]} closed ({info['owner']}{', ' + why if why else ''})")

    async def release_owner(self, owner, why="client disconnected"):
        for tid in self.owned_by(owner):
            await self.close(tid, why)

    async def reap(self):
        now = time.time()
        for tid, i in list(self.tabs.items()):
            if i["kind"] == "ws":
                continue                       # bound to a live connection
            if i["kind"] == "rest" and i["attached"]:
                if i["expires"] <= now:
                    await self.close(tid, "lease expired")
            elif i["attached"] == 0 and i["expires"] <= now:
                await self.close(tid, "never attached")
        # drop bookkeeping for tabs that vanished under us (browser restart)
        try:
            live = {t["targetId"] for t in (await self.up.send("Target.getTargets")).get("targetInfos", [])}
        except Exception:
            return
        for tid in list(self.tabs):
            if tid not in live:
                self.tabs.pop(tid, None)

    def status(self):
        now = time.time()
        return {"tabs": [{"target": t[:12], "owner": i["owner"], "kind": i["kind"],
                          "hidden": i["hidden"], "attached": i["attached"],
                          "expires_in": round(i["expires"] - now, 1) if i["kind"] != "ws" else None,
                          "lease_id": i["lease_id"]} for t, i in self.tabs.items()],
                "count": len(self.tabs)}

    def target_info(self, tid):
        """Shape a /json entry for a tab we own."""
        return {"id": tid, "type": "page", "title": "", "url": "",
                "webSocketDebuggerUrl": f"ws://127.0.0.1:{LISTEN}/devtools/page/{tid}",
                "devtoolsFrontendUrl": f"/devtools/inspector.html?ws=127.0.0.1:{LISTEN}/devtools/page/{tid}"}


REG: Registry = None
UP: Upstream = None


# ---------------------------------------------------------------------------
# the CDP-compatible front door on LISTEN
# ---------------------------------------------------------------------------
def _resp(status, reason, body=b"", ctype="application/json"):
    return Response(status, reason, Headers([("Content-Type", ctype),
                                             ("Content-Length", str(len(body))),
                                             ("Connection", "close")]), body)


def _json(obj, status=200):
    return _resp(status, "OK" if status == 200 else "Error", json.dumps(obj, indent=2).encode())


async def _pool_entries():
    """What a legacy client sees on GET /json: only unclaimed tabs we made.
    Make one if there are none so 'first page' always resolves to something."""
    free = REG.free_pool()
    if not free:
        tid = await REG.create("pool", "about:blank", hidden=True, kind="pool")
        free = [tid]
    entries = []
    try:
        infos = {t["targetId"]: t for t in (await UP.send("Target.getTargets")).get("targetInfos", [])}
    except Exception:
        infos = {}
    for tid in free:
        e = REG.target_info(tid)
        e["title"] = infos.get(tid, {}).get("title", "")
        e["url"] = infos.get(tid, {}).get("url", "")
        entries.append(e)
    return entries


async def process_request(connection, request):
    """HTTP endpoints of the DevTools remote-debugging surface. Return None to
    let a websocket handshake proceed."""
    path = request.path
    if path.startswith("/devtools/"):
        return None
    u = urlparse(path)
    # Playwright asks for /json/version/ with a trailing slash; Chrome tolerates it.
    u = u._replace(path=(u.path.rstrip("/") or "/"))
    if u.path == "/json/version":
        v = dict(UP.version or {})
        v["Browser"] = (v.get("Browser", "") + " (browser-broker)").strip()
        v["webSocketDebuggerUrl"] = f"ws://127.0.0.1:{LISTEN}/devtools/browser/{uuid.uuid4().hex}"
        return _json(v)
    if u.path in ("/json", "/json/list"):
        return _json(await _pool_entries())
    if u.path == "/json/new":
        url = unquote(u.query) if u.query else "about:blank"
        tid = await REG.create("pool", url, hidden=True, kind="pool")
        return _json(REG.target_info(tid))
    if u.path.startswith("/json/activate/"):
        return _resp(200, "OK", b"Target activated (noop)", "text/plain")
    if u.path.startswith("/json/close/"):
        tid = u.path.rsplit("/", 1)[-1]
        if tid in REG.tabs:
            await REG.close(tid, "client closed it")
            return _resp(200, "OK", b"Target is closing", "text/plain")
        return _resp(404, "Not Found")
    if u.path == "/json/protocol":
        return _json(await UP._http("/json/protocol"))
    return _resp(404, "Not Found")


async def _pump(src, dst, transform=None):
    """Copy messages src -> dst, optionally rewriting/dropping via transform."""
    try:
        async for raw in src:
            if transform:
                out = await transform(raw)
                if out is None:
                    continue
                raw = out
            await dst.send(raw)
    except websockets.ConnectionClosed:
        pass
    finally:
        try:
            await dst.close()
        except Exception:
            pass


async def handle_ws(connection):
    path = connection.request.path
    if path.startswith("/devtools/page/"):
        await _page_session(connection, path.rsplit("/", 1)[-1])
    elif path.startswith("/devtools/browser/"):
        await _browser_session(connection)
    else:
        await connection.close(1008, "unknown endpoint")


async def _page_session(client, tid):
    info = REG.tabs.get(tid)
    if info is None:
        await client.close(1008, "not your tab")
        return
    info["attached"] += 1
    owner = info["owner"]
    up = await ws_connect(f"ws://127.0.0.1:{UPSTREAM}/devtools/page/{tid}", max_size=None)

    async def c2u(raw):
        try:
            m = json.loads(raw)
        except Exception:
            return raw
        if m.get("method") in FOCUS_STEALERS:
            out = {"id": m.get("id"), "result": {}}
            if "sessionId" in m:
                out["sessionId"] = m["sessionId"]
            await client.send(json.dumps(out))
            return None
        return raw

    try:
        await asyncio.gather(_pump(client, up, c2u), _pump(up, client))
    finally:
        info = REG.tabs.get(tid)
        if info is not None:
            info["attached"] -= 1
            if info["kind"] == "pool" and info["attached"] <= 0:
                await REG.close(tid, "page socket closed")
            elif info["kind"] == "rest":
                pass    # REST lease outlives the socket; the reaper handles TTL
        log(f"page socket closed for {tid[:12]} ({owner})")


async def _browser_session(client):
    """One browser-level client == one owner. It sees only what it created."""
    owner = "ws:" + uuid.uuid4().hex[:8]
    up = await ws_connect(UP.version["webSocketDebuggerUrl"], max_size=None)
    sessions = {}        # sessionId -> targetId (flattened attach)
    log(f"browser client {owner} connected")

    def mine(tid):
        return tid in REG.tabs and REG.tabs[tid]["owner"] == owner

    async def settled_mine(tid):
        """A target Chrome just announced may be ours but not yet registered
        (createTarget reply still in flight on the control socket). Wait for it
        ONLY while this client has a create in flight — otherwise decide now, so
        a foreign tab never stalls the event pump."""
        if tid in REG.tabs:
            return mine(tid)
        if not creating["n"]:
            return False
        for _ in range(40):
            await asyncio.sleep(0.025)
            if tid in REG.tabs:
                return mine(tid)
        return False

    async def reply(m, **body):
        out = {"id": m.get("id"), **body}
        if "sessionId" in m:
            out["sessionId"] = m["sessionId"]
        await client.send(json.dumps(out))

    async def c2u(raw):
        try:
            m = json.loads(raw)
        except Exception:
            return raw
        method, mid, p = m.get("method"), m.get("id"), m.get("params") or {}
        if method in FOCUS_STEALERS:
            await reply(m, result={})
            return None
        if method == "Target.setAutoAttach":
            auto_attach["on"] = bool(p.get("autoAttach"))
        if method == "Target.createTarget":
            hidden = not p.get("_visible")
            creating["n"] += 1
            try:
                tid = await REG.create(owner, p.get("url") or "about:blank", hidden=hidden, kind="ws")
            finally:
                creating["n"] -= 1
            if auto_attach["on"]:
                # Playwright/Puppeteer expect attachedToTarget for the new page to
                # arrive BEFORE the createTarget reply. Chrome guarantees that on
                # one socket; we are two sockets, so hold the reply until the
                # attach event has gone through.
                for _ in range(80):
                    if tid in sessions.values():
                        break
                    await asyncio.sleep(0.025)
            await reply(m, result={"targetId": tid})
            # tell a discovering client its new tab exists
            if discover["on"]:
                infos = (await UP.send("Target.getTargets")).get("targetInfos", [])
                for t in infos:
                    if t["targetId"] == tid:
                        await client.send(json.dumps({"method": "Target.targetCreated", "params": {"targetInfo": t}}))
            return None
        if method == "Target.getTargets":
            infos = (await UP.send("Target.getTargets")).get("targetInfos", [])
            infos = [t for t in infos if mine(t["targetId"])]
            if not infos:
                # nothing yet -> give this client one so "first page" works
                tid = await REG.create(owner, "about:blank", hidden=True, kind="ws")
                infos = [t for t in (await UP.send("Target.getTargets")).get("targetInfos", []) if t["targetId"] == tid]
            await reply(m, result={"targetInfos": infos})
            return None
        if method == "Target.setDiscoverTargets":
            discover["on"] = bool(p.get("discover"))
            await reply(m, result={})
            if discover["on"]:
                infos = (await UP.send("Target.getTargets")).get("targetInfos", [])
                for t in infos:
                    if mine(t["targetId"]):
                        await client.send(json.dumps({"method": "Target.targetCreated", "params": {"targetInfo": t}}))
            return None
        if method in ("Target.attachToTarget", "Target.closeTarget", "Target.detachFromTarget",
                      "Browser.getWindowForTarget"):
            tid = p.get("targetId")
            if method == "Target.detachFromTarget":
                tid = sessions.get(p.get("sessionId"), tid)
            if tid and not mine(tid):
                await reply(m, error={"code": -32000, "message": "No target with given id found"})
                return None
            if method == "Target.closeTarget":
                await REG.close(tid, "client closed it")
                await reply(m, result={"success": True})
                return None
        if method == "Target.setAutoAttach" and p.get("flatten") is not None:
            pass    # allowed; auto-attach events are filtered below
        return raw

    async def u2c(raw):
        try:
            m = json.loads(raw)
        except Exception:
            return raw
        method, p, sid = m.get("method"), m.get("params") or {}, m.get("sessionId")
        if method == "Target.attachedToTarget":
            child, tid = p.get("sessionId"), (p.get("targetInfo") or {}).get("targetId")
            # nested under one of our sessions (OOPIF, worker) or one of our pages
            if sid in sessions or await settled_mine(tid):
                sessions[child] = tid
                return raw
            try:    # someone else's tab auto-attached to us: let go quietly
                await up.send(json.dumps({"id": 0, "method": "Target.detachFromTarget",
                                          "params": {"sessionId": child}}))
            except Exception:
                pass
            return None
        if method == "Target.detachedFromTarget":
            known = p.get("sessionId") in sessions
            sessions.pop(p.get("sessionId"), None)
            return raw if known else None
        if sid is not None:
            return raw if sid in sessions else None       # session traffic: ours or nothing
        if method in ("Target.targetCreated", "Target.targetInfoChanged", "Target.targetDestroyed"):
            tid = p.get("targetId") or (p.get("targetInfo") or {}).get("targetId")
            if tid in sessions.values() or await settled_mine(tid):
                return raw
            return None
        return raw

    discover = {"on": False}
    auto_attach = {"on": False}
    creating = {"n": 0}
    try:
        await asyncio.gather(_pump(client, up, c2u), _pump(up, client, u2c))
    finally:
        await REG.release_owner(owner)
        log(f"browser client {owner} disconnected")


# ---------------------------------------------------------------------------
# REST lease API on API port (kept compatible with broker_client.py)
# ---------------------------------------------------------------------------
class ApiHandler(BaseHTTPRequestHandler):
    loop = None

    def log_message(self, *a):
        pass

    def _reply(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _run(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(30)

    def do_GET(self):
        if self.path.startswith("/status"):
            s = REG.status()
            s["brave_alive"] = self._run(UP.alive())
            s["leases"] = [{"lease_id": t["lease_id"], "owner": t["owner"], "purpose": None,
                            "target": t["target"], "expires_in": t["expires_in"]}
                           for t in s["tabs"] if t["kind"] == "rest"]
            return self._reply(200, s)
        if self.path.startswith("/health"):
            return self._reply(200, {"ok": True, "brave": self._run(UP.alive())})
        self._reply(404, {"error": "no such endpoint"})

    def do_POST(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
            b = json.loads(self.rfile.read(n) or b"{}")
            if self.path.startswith("/lease"):
                ttl = int(b.get("ttl", DEFAULT_TTL))
                tid = self._run(REG.create(b.get("owner", "anonymous"), b.get("url"),
                                           hidden=bool(b.get("hidden", True)), kind="rest", ttl=ttl))
                i = REG.tabs[tid]
                i["attached"] = 1          # a REST lease counts as held from grant
                i["purpose"] = b.get("purpose")
                return self._reply(200, {"lease_id": i["lease_id"], "target_id": tid,
                                         "ws_url": f"ws://127.0.0.1:{LISTEN}/devtools/page/{tid}",
                                         "expires_in": ttl})
            if self.path.startswith("/renew"):
                for tid, i in REG.tabs.items():
                    if i.get("lease_id") == b.get("lease_id"):
                        i["expires"] = time.time() + int(b.get("ttl", DEFAULT_TTL))
                        return self._reply(200, {"lease_id": b["lease_id"], "expires_in": int(b.get("ttl", DEFAULT_TTL))})
                return self._reply(404, {"error": "unknown lease"})
            if self.path.startswith("/release"):
                for tid, i in list(REG.tabs.items()):
                    if i.get("lease_id") == b.get("lease_id"):
                        self._run(REG.close(tid, "released"))
                        return self._reply(200, {"released": b["lease_id"]})
                return self._reply(404, {"error": "unknown lease"})
            if self.path.startswith("/adopt"):
                return self._reply(410, {"error": "adopt is not supported by the proxy — agents only ever get tabs the broker made"})
            self._reply(404, {"error": "no such endpoint"})
        except Exception as e:
            self._reply(500, {"error": f"{type(e).__name__}: {e}"})


async def background():
    last_hb = 0
    while True:
        try:
            await REG.reap()
            if time.time() - last_hb > HEARTBEAT_EVERY:
                s = REG.status()
                log(f"heartbeat — {s['count']} broker tabs, browser {'up' if await UP.alive() else 'DOWN'}")
                last_hb = time.time()
        except Exception as e:
            log(f"background error: {type(e).__name__}: {e}")
        await asyncio.sleep(5)


async def main():
    global REG, UP
    UP = Upstream()
    try:
        await UP.connect()
    except Exception as e:
        log(f"no browser answering CDP on {UPSTREAM} ({e}) — launch it with "
            f"--remote-debugging-port={UPSTREAM} first (see launch-browser.sh)")
        sys.exit(1)
    REG = Registry(UP)
    loop = asyncio.get_running_loop()
    ApiHandler.loop = loop
    api = ThreadingHTTPServer(("127.0.0.1", API), ApiHandler)
    threading.Thread(target=api.serve_forever, daemon=True).start()
    asyncio.create_task(background())
    async with ws_serve(handle_ws, "127.0.0.1", LISTEN, process_request=process_request,
                        max_size=None, ping_interval=None):
        log(f"proxy up: CDP on {LISTEN} (upstream browser {UPSTREAM}), lease API on {API}")
        await asyncio.Future()


if __name__ == "__main__":
    asyncio.run(main())
