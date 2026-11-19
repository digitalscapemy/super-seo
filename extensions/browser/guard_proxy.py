"""guard_proxy.py - local filtering HTTP/CONNECT proxy that enforces the network policy for a headless browser.

Chrome is started with --proxy-server pointing here, --proxy-bypass-list=<-loopback> (so even loopback goes through
the proxy), --disable-quic and a WebRTC policy that blocks non-proxied UDP. Every connection from every frame, worker,
service worker or WebSocket therefore passes through this proxy, which:

  1. resolves the target host ONCE,
  2. refuses unless EVERY resolved address is public (ip_is_public from crawl_audit) — mixed records are refused,
  3. connects to that exact vetted IP itself (the browser never resolves hosts when a proxy is set),
  4. re-checks the connected peer with getpeername() before relaying a single byte.

Because the proxy performs the only DNS lookup and connects to the address it checked, DNS rebinding cannot change
the target between check and use. TLS stays end-to-end (CONNECT tunnel); the proxy never sees page contents for https.
Standard library only.
"""
from __future__ import annotations

import select
import socket
import socketserver
import threading

ALLOWED_PORTS = {80, 443, 8080, 8443}
IDLE_TIMEOUT = 30


class _Handler(socketserver.BaseRequestHandler):
    def handle(self):
        srv: "GuardProxy" = self.server.guard  # type: ignore[attr-defined]
        client = self.request
        client.settimeout(IDLE_TIMEOUT)
        try:
            head = b""
            while b"\r\n\r\n" not in head and len(head) < 65536:
                chunk = client.recv(4096)
                if not chunk:
                    return
                head += chunk
            header_block, _, rest = head.partition(b"\r\n\r\n")
            lines = header_block.decode("latin-1").split("\r\n")
            method, target, version = (lines[0].split(" ") + ["", "", ""])[:3]
            if method.upper() == "CONNECT":
                host, _, port = target.rpartition(":")
                port_i = int(port) if port.isdigit() else 443
                upstream = srv.open_upstream(host.strip("[]"), port_i)
                if isinstance(upstream, str):
                    client.sendall(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n")
                    return
                client.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
                if rest:
                    upstream.sendall(rest)
                self._relay(client, upstream)
            else:
                # plain http: absolute-form request line -> origin-form
                if not target.lower().startswith("http://"):
                    client.sendall(b"HTTP/1.1 400 Bad Request\r\nContent-Length: 0\r\n\r\n")
                    return
                hostport, _, path = target[7:].partition("/")
                host, _, port = hostport.rpartition(":") if ":" in hostport.split("]")[-1] else (hostport, "", "80")
                port_i = int(port) if port.isdigit() else 80
                upstream = srv.open_upstream(host.strip("[]"), port_i)
                if isinstance(upstream, str):
                    body = b"blocked by super-seo network guard"
                    client.sendall(b"HTTP/1.1 403 Forbidden\r\nContent-Type: text/plain\r\nContent-Length: "
                                   + str(len(body)).encode() + b"\r\nConnection: close\r\n\r\n" + body)
                    return
                kept = [l for l in lines[1:] if not l.lower().startswith(("proxy-connection:", "proxy-authorization:"))]
                req = f"{method} /{path} {version}\r\n" + "\r\n".join(kept) + "\r\n\r\n"
                upstream.sendall(req.encode("latin-1") + rest)
                self._relay(client, upstream)
        except (OSError, ValueError):
            return
        finally:
            try:
                client.close()
            except OSError:
                pass

    @staticmethod
    def _relay(a: socket.socket, b: socket.socket):
        try:
            socks = [a, b]
            while True:
                r, _, x = select.select(socks, [], socks, IDLE_TIMEOUT)
                if x or not r:
                    return
                for s in r:
                    data = s.recv(65536)
                    if not data:
                        return
                    (b if s is a else a).sendall(data)
        except OSError:
            return
        finally:
            try:
                b.close()
            except OSError:
                pass


class _Server(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True


class GuardProxy:
    """Start with .start(); pass .chrome_flags() to the browser; .stop() when done. .blocked lists refusals."""

    def __init__(self, ip_is_public, allow_ports=ALLOWED_PORTS):
        self.ip_is_public = ip_is_public
        self.allow_ports = set(allow_ports)
        self.blocked: list[dict] = []
        self._lock = threading.Lock()
        self._srv: _Server | None = None

    def _refuse(self, host, port, reason) -> str:
        with self._lock:
            if len(self.blocked) < 200:
                self.blocked.append({"url": f"{host}:{port}", "reason": reason})
        return reason

    def open_upstream(self, host: str, port: int):
        """Return a connected socket to a vetted public address, or a refusal reason string."""
        if port not in self.allow_ports:
            return self._refuse(host, port, f"port {port} not allowed")
        try:
            infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        except OSError as e:
            return self._refuse(host, port, f"DNS failed: {e}")
        addrs = [i[4][0] for i in infos]
        if not addrs or not all(self.ip_is_public(a) for a in addrs):
            return self._refuse(host, port, f"non-public address ({', '.join(sorted(set(addrs))[:3])})")
        fam, _, _, _, sockaddr = infos[0]
        s = socket.socket(fam, socket.SOCK_STREAM)
        s.settimeout(15)
        try:
            s.connect(sockaddr)   # connect to the exact address that was checked
            peer = s.getpeername()[0]
            if not self.ip_is_public(peer):
                s.close()
                return self._refuse(host, port, f"connected peer {peer} is not public")
        except OSError as e:
            s.close()
            return self._refuse(host, port, f"connect failed: {e}")
        s.settimeout(IDLE_TIMEOUT)
        return s

    def start(self) -> int:
        self._srv = _Server(("127.0.0.1", 0), _Handler)
        self._srv.guard = self  # type: ignore[attr-defined]
        threading.Thread(target=self._srv.serve_forever, daemon=True).start()
        return self._srv.server_address[1]

    @property
    def port(self) -> int:
        return self._srv.server_address[1] if self._srv else 0

    def chrome_flags(self) -> list[str]:
        return [f"--proxy-server=http://127.0.0.1:{self.port}",
                "--proxy-bypass-list=<-loopback>",          # loopback must go through the proxy too (and be refused)
                "--disable-quic",                           # no UDP/HTTP3 side channel
                "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
                "--dns-prefetch-disable"]

    def stop(self):
        if self._srv:
            self._srv.shutdown()
            self._srv.server_close()
            self._srv = None
