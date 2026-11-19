"""Offline tests for the browser network-guard proxy (enforcement layer for public start URLs)."""
import socket
import sys
import threading
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "extensions" / "browser"))
sys.path.insert(0, str(ROOT / "scripts"))

import guard_proxy  # noqa: E402
from crawl_audit import ip_is_public  # noqa: E402


def addr(ip, port=443):
    fam = socket.AF_INET6 if ":" in ip else socket.AF_INET
    return (fam, socket.SOCK_STREAM, 6, "", (ip, port))


class OpenUpstreamTests(unittest.TestCase):
    def setUp(self):
        self.p = guard_proxy.GuardProxy(ip_is_public)

    def test_private_only_refused(self):
        with mock.patch("socket.getaddrinfo", return_value=[addr("10.0.0.5")]):
            self.assertIn("non-public", self.p.open_upstream("internal.test", 443))
        self.assertEqual(len(self.p.blocked), 1)

    def test_mixed_records_refused(self):
        with mock.patch("socket.getaddrinfo", return_value=[addr("93.184.216.34"), addr("127.0.0.1")]):
            self.assertIn("non-public", self.p.open_upstream("rebind.test", 443))

    def test_metadata_and_mapped_refused(self):
        for ip in ("169.254.169.254", "::ffff:127.0.0.1", "100.64.0.1", "::1", "0.0.0.0"):
            with mock.patch("socket.getaddrinfo", return_value=[addr(ip)]):
                self.assertIsInstance(self.p.open_upstream("x.test", 443), str, ip)

    def test_disallowed_port_refused(self):
        self.assertIn("port", self.p.open_upstream("example.com", 22))

    def test_dns_failure_refused(self):
        with mock.patch("socket.getaddrinfo", side_effect=OSError("nope")):
            self.assertIn("DNS failed", self.p.open_upstream("nx.test", 443))

    def test_connects_to_vetted_ip_and_checks_peer(self):
        fake = mock.MagicMock()
        fake.getpeername.return_value = ("93.184.216.34", 443)
        with mock.patch("socket.getaddrinfo", return_value=[addr("93.184.216.34")]), \
                mock.patch("socket.socket", return_value=fake):
            s = self.p.open_upstream("example.com", 443)
        self.assertIs(s, fake)
        fake.connect.assert_called_once_with(("93.184.216.34", 443))  # the exact address that was checked

    def test_peer_mismatch_refused(self):
        fake = mock.MagicMock()
        fake.getpeername.return_value = ("127.0.0.1", 443)
        with mock.patch("socket.getaddrinfo", return_value=[addr("93.184.216.34")]), \
                mock.patch("socket.socket", return_value=fake):
            self.assertIn("not public", self.p.open_upstream("example.com", 443))


class ProxyServerTests(unittest.TestCase):
    def test_connect_to_private_gets_403(self):
        p = guard_proxy.GuardProxy(ip_is_public)
        port = p.start()
        try:
            c = socket.create_connection(("127.0.0.1", port), timeout=5)
            c.sendall(b"CONNECT 127.0.0.1:443 HTTP/1.1\r\nHost: 127.0.0.1:443\r\n\r\n")
            self.assertIn(b"403", c.recv(200))
            c.close()
            c = socket.create_connection(("127.0.0.1", port), timeout=5)
            c.sendall(b"GET http://127.0.0.1:8080/secret HTTP/1.1\r\nHost: 127.0.0.1:8080\r\n\r\n")
            self.assertIn(b"403", c.recv(200))
            c.close()
        finally:
            p.stop()

    def test_chrome_flags_force_everything_through_proxy(self):
        p = guard_proxy.GuardProxy(ip_is_public)
        p.start()
        try:
            flags = p.chrome_flags()
            self.assertIn(f"--proxy-server=http://127.0.0.1:{p.port}", flags)
            self.assertIn("--proxy-bypass-list=<-loopback>", flags)
            self.assertIn("--disable-quic", flags)
            self.assertTrue(any("disable_non_proxied_udp" in f for f in flags))
        finally:
            p.stop()


if __name__ == "__main__":
    unittest.main()
