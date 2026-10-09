"""HTTP for the optional AI client: IPv4 first, IPv6 still reachable (#247).

urllib connects through `socket.create_connection`, which tries the addresses in
`getaddrinfo` order -- IPv6 first -- and gives each one the whole request
timeout. On a network whose DNS hands out IPv6 addresses with no working IPv6
route, every connect then hangs until Windows gives up (WinError 10060, ~21 s)
once per IPv6 address before IPv4 is tried: ~43 s on every AI call to
api.openai.com, measured 2026-10-09, while IPv4 connected in 0.04 s.

So IPv4 addresses go first, and every address but the last gets at most
`FALLBACK_CONNECT_TIMEOUT` to connect; the last keeps the full timeout, so an
IPv6-only network, or a slow but working one, still connects. Nothing here is on
a path a translator hits without asking: only the human-invoked AI features use
it (the offline invariant is unchanged).
"""
from __future__ import annotations

import http.client
import socket
import urllib.request
from typing import Any, Callable, Sequence

FALLBACK_CONNECT_TIMEOUT = 5.0

AddrInfo = tuple[Any, Any, int, str, Any]


def connection_plan(
    infos: Sequence[AddrInfo], timeout: float | None, fallback_timeout: float | None = None,
) -> list[tuple[AddrInfo, float | None]]:
    """Each address with the connect timeout it gets, in the order tried:
    IPv4 first, `getaddrinfo` order kept within a family."""
    if fallback_timeout is None:
        fallback_timeout = FALLBACK_CONNECT_TIMEOUT
    ordered = sorted(infos, key=lambda info: info[0] != socket.AF_INET)
    plan: list[tuple[AddrInfo, float | None]] = []
    for index, info in enumerate(ordered):
        if index == len(ordered) - 1:
            plan.append((info, timeout))
        else:
            plan.append((info, fallback_timeout if timeout is None else min(timeout, fallback_timeout)))
    return plan


def create_connection(
    address: tuple[str, int], timeout: Any = socket._GLOBAL_DEFAULT_TIMEOUT,
    source_address: tuple[str, int] | None = None, *, all_errors: bool = False,
    resolve: Callable[..., list[AddrInfo]] = socket.getaddrinfo,
) -> socket.socket:
    """`socket.create_connection`'s contract, with `connection_plan`'s order
    and per-address timeouts."""
    host, port = address
    request_timeout = None if timeout is socket._GLOBAL_DEFAULT_TIMEOUT else timeout
    last_error: OSError | None = None
    for (family, kind, proto, _canonical, sockaddr), connect_timeout in connection_plan(
        resolve(host, port, 0, socket.SOCK_STREAM), request_timeout,
    ):
        sock = socket.socket(family, kind, proto)
        try:
            sock.settimeout(connect_timeout)
            if source_address:
                sock.bind(source_address)
            sock.connect(sockaddr)
            sock.settimeout(request_timeout)
            return sock
        except OSError as exc:
            sock.close()
            last_error = exc
    raise last_error or OSError(f"getaddrinfo returned no addresses for {host!r}")


class _HTTPConnection(http.client.HTTPConnection):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._create_connection = create_connection


class _HTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._create_connection = create_connection


class _HTTPHandler(urllib.request.HTTPHandler):
    def http_open(self, req: urllib.request.Request) -> http.client.HTTPResponse:
        return self.do_open(_HTTPConnection, req)


class _HTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, req: urllib.request.Request) -> http.client.HTTPResponse:
        return self.do_open(_HTTPSConnection, req, context=self._context)


# build_opener drops its default HTTP(S) handlers when given subclasses of them;
# proxies, redirects and HTTPError handling are the defaults, as with urlopen.
_OPENER = urllib.request.build_opener(_HTTPHandler(), _HTTPSHandler())


def urlopen(req: urllib.request.Request, timeout: float) -> Any:
    """`urllib.request.urlopen(req, timeout=...)`, connecting IPv4 first."""
    return _OPENER.open(req, timeout=timeout)
