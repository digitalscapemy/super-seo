"""cdp_lite.py - minimal Chrome DevTools Protocol client (stdlib only) used for true mobile emulation.

Why: Chrome/Edge in --headless=new enforce a minimum window width (~500 CSS px), so --window-size=390,844 alone
cannot produce a phone-width layout. Emulation.setDeviceMetricsOverride can. The browser is started with
--remote-debugging-port=0 (bound to 127.0.0.1, random port) and a throwaway profile, driven over a local
websocket, then closed and the profile deleted.

Only the local DevTools endpoint is contacted by this module; the page itself is loaded by the browser.
"""
from __future__ import annotations

import base64
import json
import os
import shutil
import socket
import struct
import subprocess
import tempfile
import time
from urllib import request
from urllib.parse import urlparse


class CDPError(RuntimeError):
    pass


def launch_argv(browser: str, profile_dir: str, extra_flags: list[str] | None = None) -> list[str]:
    return [browser, "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
            f"--user-data-dir={profile_dir}", "--remote-debugging-port=0", "--remote-debugging-address=127.0.0.1",
            "--hide-scrollbars", "--disable-extensions", "--mute-audio", *(extra_flags or []), "about:blank"]


class _WS:
    """Tiny RFC 6455 client: text frames, client masking, fragmentation, ping/pong."""

    def __init__(self, ws_url: str, timeout: float):
        u = urlparse(ws_url)
        if u.hostname not in ("127.0.0.1", "localhost", "::1"):
            raise CDPError("refusing non-local DevTools endpoint")
        self.sock = socket.create_connection((u.hostname, u.port or 80), timeout=timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        path = u.path + (f"?{u.query}" if u.query else "")
        self.sock.sendall((f"GET {path} HTTP/1.1\r\nHost: {u.hostname}:{u.port}\r\nUpgrade: websocket\r\n"
                           f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise CDPError("websocket handshake failed")
            buf += chunk
        head, self._rest = buf.split(b"\r\n\r\n", 1)
        if b" 101 " not in head.split(b"\r\n", 1)[0]:
            raise CDPError("websocket upgrade refused")

    def _recv_exact(self, n: int) -> bytes:
        out = bytearray()
        if self._rest:
            out += self._rest[:n]
            self._rest = self._rest[n:]
        while len(out) < n:
            chunk = self.sock.recv(min(1 << 20, n - len(out)))
            if not chunk:
                raise CDPError("websocket closed")
            out += chunk
        return bytes(out)

    def send(self, text: str) -> None:
        data = text.encode("utf-8")
        hdr = bytearray([0x81])
        n = len(data)
        if n < 126:
            hdr.append(0x80 | n)
        elif n < 65536:
            hdr.append(0x80 | 126)
            hdr += struct.pack(">H", n)
        else:
            hdr.append(0x80 | 127)
            hdr += struct.pack(">Q", n)
        mask = os.urandom(4)
        hdr += mask
        self.sock.sendall(bytes(hdr) + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

    def _send_frame(self, opcode: int, payload: bytes) -> None:
        mask = os.urandom(4)
        self.sock.sendall(bytes([0x80 | opcode, 0x80 | len(payload)]) + mask
                          + bytes(b ^ mask[i % 4] for i, b in enumerate(payload)))

    def recv(self) -> str:
        parts = bytearray()
        while True:
            b1, b2 = self._recv_exact(2)
            fin, opcode, n = b1 & 0x80, b1 & 0x0F, b2 & 0x7F
            if n == 126:
                n = struct.unpack(">H", self._recv_exact(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", self._recv_exact(8))[0]
            if b2 & 0x80:
                m = self._recv_exact(4)
                payload = bytes(b ^ m[i % 4] for i, b in enumerate(self._recv_exact(n)))
            else:
                payload = self._recv_exact(n)
            if opcode == 0x9:
                self._send_frame(0xA, payload[:125])
                continue
            if opcode == 0x8:
                raise CDPError("websocket closed by browser")
            if opcode in (0x0, 0x1, 0x2):
                parts += payload
                if fin:
                    return parts.decode("utf-8", errors="replace")

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


class Session:
    def __init__(self, ws: _WS, on_event=None):
        self.ws, self._id, self.events = ws, 0, []
        self.on_event = on_event  # called for every event; returns True if it consumed the event

    def send_nowait(self, method: str, params: dict | None = None) -> None:
        """Fire-and-forget command (its reply is ignored). Used from inside event handlers."""
        self._id += 1
        self.ws.send(json.dumps({"id": self._id, "method": method, "params": params or {}}))

    def _dispatch(self, msg: dict) -> None:
        if self.on_event and self.on_event(self, msg):
            return
        self.events.append(msg)

    def call(self, method: str, params: dict | None = None, timeout: float = 30.0) -> dict:
        self._id += 1
        mid = self._id
        self.ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        deadline = time.time() + timeout
        while time.time() < deadline:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == mid:
                if "error" in msg:
                    raise CDPError(f"{method}: {msg['error'].get('message')}")
                return msg.get("result", {})
            if "method" in msg:
                self._dispatch(msg)
        raise CDPError(f"{method}: timed out")

    def wait_event(self, name: str, timeout: float) -> bool:
        if any(e.get("method") == name for e in self.events):
            return True
        deadline = time.time() + timeout
        old = self.ws.sock.gettimeout()
        try:
            while time.time() < deadline:
                self.ws.sock.settimeout(max(0.1, deadline - time.time()))
                try:
                    msg = json.loads(self.ws.recv())
                except (socket.timeout, TimeoutError):
                    return False
                if "method" in msg:
                    self._dispatch(msg)
                if msg.get("method") == name:
                    return True
        finally:
            self.ws.sock.settimeout(old)
        return False


def _read_port(profile: str, proc: subprocess.Popen, timeout: float) -> int:
    path = os.path.join(profile, "DevToolsActivePort")
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            raise CDPError(f"browser exited early (code {proc.returncode})")
        try:
            with open(path, encoding="utf-8") as fh:
                first = fh.readline().strip()
            if first.isdigit():
                return int(first)
        except OSError:
            pass
        time.sleep(0.1)
    raise CDPError("DevTools port not reported in time")


def make_network_guard(url_reason, ip_ok):
    """Event handler enforcing the network policy inside the browser.

    url_reason(url) -> str | None : refusal reason for a request URL (scheme + resolved-host check); None = allowed.
    ip_ok(ip) -> bool             : True if the address the browser actually connected to is public.
    This is the screening/reporting layer for the page target (schemes, early refusals). ENFORCEMENT happens in
    guard_proxy.GuardProxy: every connection from every frame, worker and protocol goes through it, it performs the
    only DNS lookup, refuses non-public addresses and connects to the exact IP it checked (no rebinding window).
    """
    state = {"blocked": [], "violations": []}

    def handler(sess, msg):
        m, prm = msg.get("method"), msg.get("params") or {}
        if m == "Fetch.requestPaused":
            req_url = (prm.get("request") or {}).get("url", "")
            scheme = req_url.split(":", 1)[0].lower()
            if scheme in ("data", "blob"):
                reason = None
            else:
                reason = url_reason(req_url)   # evaluated per request; never cached for the run
            if reason:
                state["blocked"].append({"url": req_url[:200], "reason": reason})
                sess.send_nowait("Fetch.failRequest", {"requestId": prm.get("requestId"), "errorReason": "BlockedByClient"})
            else:
                sess.send_nowait("Fetch.continueRequest", {"requestId": prm.get("requestId")})
            return True
        return False

    return handler, state


def ip_ok_for_proxy(ip: str) -> bool:
    """Public-address rule used by the proxy (same allow-list as crawl_audit.ip_is_public)."""
    import sys as _sys
    _sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "scripts"))
    from crawl_audit import ip_is_public
    return ip_is_public(ip)


def emulate(browser: str, url: str, width: int, height: int, user_agent: str | None = None, mobile: bool = True,
            screenshot_path: str | None = None, want_dom: bool = False, settle_ms: int = 2000,
            timeout: float = 60.0, network_guard=None) -> dict:
    """Load url with device emulation; optionally save a PNG and/or return the rendered DOM.

    network_guard: (handler, state) from make_network_guard(). When given, every browser request is intercepted and
    checked, and the result is discarded if the browser ever connected to a non-public address."""
    profile = tempfile.mkdtemp(prefix="super-seo-cdp-")
    proc = None
    ws = None
    proxy = None
    out: dict = {"error": None, "html": None, "screenshot": None, "viewport": f"{width}x{height}"}
    start = time.time()
    try:
        if network_guard:
            from guard_proxy import GuardProxy  # local import: same folder
            proxy = GuardProxy(ip_ok_for_proxy)
            proxy.start()
        flags = proxy.chrome_flags() if proxy else None
        proc = subprocess.Popen(launch_argv(browser, profile, flags), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        port = _read_port(profile, proc, 20)
        opener = request.build_opener(request.ProxyHandler({}))  # local endpoint: never via a proxy
        with opener.open(f"http://127.0.0.1:{port}/json/list", timeout=10) as r:
            targets = json.loads(r.read().decode("utf-8"))
        page = next((t for t in targets if t.get("type") == "page" and t.get("webSocketDebuggerUrl")), None)
        if not page:
            raise CDPError("no page target")
        ws = _WS(page["webSocketDebuggerUrl"], timeout=30)
        s = Session(ws, on_event=network_guard[0] if network_guard else None)
        if network_guard:
            s.call("Fetch.enable", {"patterns": [{"urlPattern": "*", "requestStage": "Request"}]})
        s.call("Page.enable")
        s.call("Emulation.setDeviceMetricsOverride",
               {"width": width, "height": height, "deviceScaleFactor": 1, "mobile": mobile})
        if mobile:
            s.call("Emulation.setTouchEmulationEnabled", {"enabled": True, "maxTouchPoints": 5})
        if user_agent:
            s.call("Emulation.setUserAgentOverride", {"userAgent": user_agent})
        nav = s.call("Page.navigate", {"url": url})
        if nav.get("errorText"):
            raise CDPError(f"navigation failed: {nav['errorText']}")
        remaining = max(5.0, timeout - (time.time() - start) - 10)
        if not s.wait_event("Page.loadEventFired", remaining):
            out["note"] = "load event not fired before timeout; captured current state"
        time.sleep(settle_ms / 1000.0)
        if screenshot_path:
            data = s.call("Page.captureScreenshot", {"format": "png"}).get("data", "")
            with open(screenshot_path, "wb") as fh:
                fh.write(base64.b64decode(data))
            out["screenshot"] = screenshot_path
        if want_dom:
            r = s.call("Runtime.evaluate", {"expression": "document.documentElement.outerHTML", "returnByValue": True})
            out["html"] = "<!DOCTYPE html>\n" + str((r.get("result") or {}).get("value") or "")
        if network_guard:
            loc = s.call("Runtime.evaluate", {"expression": "location.href", "returnByValue": True})
            final_url = str((loc.get("result") or {}).get("value") or "")
            out["final_url"] = final_url
            if final_url and final_url.split(":", 1)[0] not in ("http", "https", "about", "chrome-error"):
                network_guard[1]["violations"].append({"url": final_url[:200], "ip": "non-http final document"})
            out["blocked_requests"] = network_guard[1]["blocked"]
            if network_guard[1]["violations"]:
                # Poisoned run: never return anything that came from a private address.
                if out.get("screenshot") and os.path.exists(out["screenshot"]):
                    os.remove(out["screenshot"])
                v = network_guard[1]["violations"][0]
                out.update(html=None, screenshot=None,
                           error=f"refused: the browser reached a private/internal address ({v['ip']}); output discarded")
        try:
            s.call("Browser.close", timeout=5)
        except (CDPError, OSError):
            pass
    except (CDPError, OSError, ValueError) as e:
        out["error"] = f"devtools emulation failed: {e}"
    finally:
        if ws:
            ws.close()
        if proxy:
            proxy.stop()
        if proc and proc.poll() is None:
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass
        for _ in range(10):
            shutil.rmtree(profile, ignore_errors=True)
            if not os.path.exists(profile):
                break
            time.sleep(0.5)
    if network_guard:
        # Always report what the guard did, also when navigation itself was blocked (error path).
        out["blocked_requests"] = network_guard[1]["blocked"] + (proxy.blocked if proxy else [])
        if proxy and proxy.blocked and not out.get("error"):
            out["notes"] = f"network guard refused {len(proxy.blocked)} connection(s) to non-public addresses"
        if out.get("error") and network_guard[1]["blocked"] and not str(out["error"]).startswith("refused"):
            first = network_guard[1]["blocked"][0]
            out["error"] = f"refused: browser request blocked by the network guard ({first['reason']}: {first['url']})"
    return out
