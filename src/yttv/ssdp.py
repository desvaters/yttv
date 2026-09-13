"""SSDP: the UDP multicast search that DIAL devices answer to.

Only the transport lives here. It sends an ``M-SEARCH`` to
``239.255.255.250:1900`` and collects the unicast replies for a while.
Standard library only, so the doctor can use it without any extra.

The replies are HTTP responses in a datagram; :class:`http.client.HTTPResponse`
parses them given anything with a ``makefile()``.
"""

from __future__ import annotations

import http.client
import io
import socket
import time
from dataclasses import dataclass, field

MULTICAST_GROUP = "239.255.255.250"
MULTICAST_PORT = 1900
ST_DIAL = "urn:dial-multiscreen-org:service:dial:1"
ST_ALL = "ssdp:all"


@dataclass(frozen=True)
class Response:
    """One reply to an M-SEARCH."""

    address: str
    location: str
    usn: str
    st: str
    server: str = ""
    wakeup: dict[str, str] = field(default_factory=dict)
    headers: dict[str, str] = field(default_factory=dict)


class _Datagram:
    """Just enough of a socket for HTTPResponse: ``makefile()``."""

    def __init__(self, data: bytes):
        self._data = data

    def makefile(self, mode: str = "rb", *args: object, **kwargs: object) -> io.BytesIO:
        return io.BytesIO(self._data)


def parse_response(data: bytes, address: str) -> Response | None:
    """Parse one reply datagram. ``None`` for anything that is not a
    ``200 OK`` with a LOCATION."""
    try:
        parsed = http.client.HTTPResponse(_Datagram(data))  # type: ignore[arg-type]
        parsed.begin()
    except (http.client.HTTPException, ValueError, OSError):
        return None
    if parsed.status != 200:
        return None
    headers = {k.lower(): v.strip() for k, v in parsed.getheaders()}
    location = headers.get("location", "")
    if not location:
        return None
    return Response(
        address=address,
        location=location,
        usn=headers.get("usn", ""),
        st=headers.get("st", ""),
        server=headers.get("server", ""),
        wakeup=parse_wakeup(headers.get("wakeup", "")),
        headers=headers,
    )


def parse_wakeup(value: str) -> dict[str, str]:
    """``MAC=aa:bb:cc:dd:ee:ff;Timeout=10`` to ``{"mac": ..., "timeout": ...}``.
    Keys are lower-cased; the value is what the device sent."""
    out: dict[str, str] = {}
    for part in value.split(";"):
        if "=" in part:
            key, val = part.split("=", 1)
            out[key.strip().lower()] = val.strip()
    return out


def build_search(st: str, mx: int, host: str = MULTICAST_GROUP, port: int = MULTICAST_PORT) -> bytes:
    return (
        "M-SEARCH * HTTP/1.1\r\n"
        f"HOST: {host}:{port}\r\n"
        'MAN: "ssdp:discover"\r\n'
        f"MX: {mx}\r\n"
        f"ST: {st}\r\n"
        "\r\n"
    ).encode("ascii")


def search(
    st: str = ST_DIAL,
    *,
    timeout: float = 4.0,
    local_address: str | None = None,
    target: tuple[str, int] = (MULTICAST_GROUP, MULTICAST_PORT),
    unicast_hosts: list[str] | None = None,
) -> list[Response]:
    """Send an M-SEARCH and collect replies for ``timeout`` seconds.

    ``local_address`` binds the socket to one interface, for machines with
    several. ``unicast_hosts`` additionally sends the search straight to
    those hosts, which sidesteps multicast trouble. Duplicate replies (same
    USN from the same address) are dropped.
    """
    mx = max(1, min(int(timeout), 5))
    message = build_search(st, mx, *target)
    responses: list[Response] = []
    seen: set[tuple[str, str]] = set()

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
        sock.bind((local_address or "", 0))
        sock.sendto(message, target)
        for host in unicast_hosts or []:
            sock.sendto(build_search(st, mx, host, target[1]), (host, target[1]))

        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            sock.settimeout(remaining)
            try:
                data, (address, _port) = sock.recvfrom(65535)
            except socket.timeout:
                break
            except OSError:
                break
            response = parse_response(data, address)
            if response is None:
                continue
            key = (address, response.usn)
            if key in seen:
                continue
            seen.add(key)
            responses.append(response)
    return responses
