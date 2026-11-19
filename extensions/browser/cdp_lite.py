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


def launch_argv(browser: str, profile_dir: str) -> list[str]:
    return [browser, "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
            f"--user-data-dir={profile_dir}", "--remote-debugging-port=0", "--remote-debugging-address=127.0.0.1",
            "--hide-scrollbars", "--disable-extensions", "--mute-audio", "about:blank"]


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
    def __init__(self, ws: _WS):
        self.ws, self._id, self.events = ws, 0, []

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
                self.events.append(msg)
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
                if msg.get("method") == name:
                    return True
                if "method" in msg:
                    self.events.append(msg)
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


def emulate(browser: str, url: str, width: int, height: int, user_agent: str | None = None, mobile: bool = True,
            screenshot_path: str | None = None, want_dom: bool = False, settle_ms: int = 2000,
            timeout: float = 60.0) -> dict:
    """Load url with device emulation; optionally save a PNG and/or return the rendered DOM."""
    profile = tempfile.mkdtemp(prefix="super-seo-cdp-")
    proc = None
    ws = None
    out: dict = {"error": None, "html": None, "screenshot": None, "viewport": f"{width}x{height}"}
    start = time.time()
    try:
        proc = subprocess.Popen(launch_argv(browser, profile), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        port = _read_port(profile, proc, 20)
        opener = request.build_opener(request.ProxyHandler({}))  # local endpoint: never via a proxy
        with opener.open(f"http://127.0.0.1:{port}/json/list", timeout=10) as r:
            targets = json.loads(r.read().decode("utf-8"))
        page = next((t for t in targets if t.get("type") == "page" and t.get("webSocketDebuggerUrl")), None)
        if not page:
            raise CDPError("no page target")
        ws = _WS(page["webSocketDebuggerUrl"], timeout=30)
        s = Session(ws)
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
        try:
            s.call("Browser.close", timeout=5)
        except (CDPError, OSError):
            pass
    except (CDPError, OSError, ValueError) as e:
        out["error"] = f"devtools emulation failed: {e}"
    finally:
        if ws:
            ws.close()
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
    return out
