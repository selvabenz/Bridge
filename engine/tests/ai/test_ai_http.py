"""The AI client's connections try IPv4 first and cap every address but the last
(#247): an unroutable IPv6 address cost ~21 s per AI call before IPv4 was tried."""
from __future__ import annotations

import http.server
import socket
import threading
import time
import urllib.request

import pytest

from tc_ai_bridge import ai_http


def _info(family, address):
    return (family, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", address)


V6 = _info(socket.AF_INET6, ("2001:db8::1", 443, 0, 0))
V6_B = _info(socket.AF_INET6, ("2001:db8::2", 443, 0, 0))
V4 = _info(socket.AF_INET, ("192.0.2.1", 443))
V4_B = _info(socket.AF_INET, ("192.0.2.2", 443))


def test_ipv4_goes_first_and_only_the_last_address_keeps_the_full_timeout():
    plan = ai_http.connection_plan([V6, V4, V6_B, V4_B], 240.0, fallback_timeout=5.0)
    assert [info for info, _ in plan] == [V4, V4_B, V6, V6_B]
    assert [timeout for _, timeout in plan] == [5.0, 5.0, 5.0, 240.0]


def test_a_short_request_timeout_is_never_lengthened_and_none_is_capped_but_kept_last():
    assert [t for _, t in ai_http.connection_plan([V6, V4], 2.0, fallback_timeout=5.0)] == [2.0, 2.0]
    assert [t for _, t in ai_http.connection_plan([V6, V4], None, fallback_timeout=5.0)] == [5.0, None]
    assert [t for _, t in ai_http.connection_plan([V6], 30.0, fallback_timeout=5.0)] == [30.0]


@pytest.fixture
def listener():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen()
    yield server.getsockname()
    server.close()


def test_a_dead_first_address_costs_at_most_the_fallback_timeout(monkeypatch, listener):
    """192.0.2.1 (TEST-NET-1) is never routed: it either hangs, as the IPv6
    addresses did, or fails at once. Either way the next address is reached."""
    monkeypatch.setattr(ai_http, "FALLBACK_CONNECT_TIMEOUT", 0.5)
    resolve = lambda *_args: [_info(socket.AF_INET, ("192.0.2.1", 9)), _info(socket.AF_INET, listener)]
    started = time.monotonic()
    sock = ai_http.create_connection(("api.example", 443), 60.0, resolve=resolve)
    try:
        assert sock.getpeername() == listener
        assert sock.gettimeout() == 60.0
    finally:
        sock.close()
    assert time.monotonic() - started < 3.0


def test_ipv6_is_still_reached_when_ipv4_fails():
    try:
        server6 = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
        server6.bind(("::1", 0))
    except OSError:
        pytest.skip("no IPv6 loopback on this machine")
    server6.listen()
    closed = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    closed.bind(("127.0.0.1", 0))
    dead_port = closed.getsockname()[1]
    closed.close()  # nothing listens there now: the IPv4 attempt is refused
    resolve = lambda *_args: [_info(socket.AF_INET6, server6.getsockname()),
                              _info(socket.AF_INET, ("127.0.0.1", dead_port))]
    try:
        sock = ai_http.create_connection(("api.example", 443), 10.0, resolve=resolve)
        assert sock.family == socket.AF_INET6
        sock.close()
    finally:
        server6.close()


def test_no_address_at_all_is_an_oserror():
    with pytest.raises(OSError):
        ai_http.create_connection(("api.example", 443), 5.0, resolve=lambda *_args: [])


def test_urlopen_sends_the_request_through_the_ipv4_first_connector(monkeypatch):
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"echo:" + body)

        def log_message(self, *_args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    used = []
    real = ai_http.create_connection

    def spy(address, *args, **kwargs):
        used.append(address)
        return real(address, *args, **kwargs)

    monkeypatch.setattr(ai_http, "create_connection", spy)
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}/v1/responses"
        request = urllib.request.Request(url, data=b"hi", method="POST")
        with ai_http.urlopen(request, 10.0) as response:
            assert (response.status, response.read()) == (200, b"echo:hi")
    finally:
        server.shutdown()
        server.server_close()
    assert used == [("127.0.0.1", server.server_address[1])]
    assert any(isinstance(h, ai_http._HTTPSHandler) for h in ai_http._OPENER.handlers)
