from __future__ import annotations

import socket
import threading
from pathlib import Path

from yttv import ssdp

FIXTURES = Path(__file__).parent / "fixtures"
DIAL_REPLY = (FIXTURES / "ssdp_dial_response.txt").read_bytes()


def test_parse_dial_reply() -> None:
    r = ssdp.parse_response(DIAL_REPLY, "192.168.178.50")
    assert r is not None
    assert r.address == "192.168.178.50"
    assert r.location == "http://192.168.178.50:8060/dd.xml"
    assert r.st == ssdp.ST_DIAL
    assert r.usn.startswith("uuid:00000000-0000-4000-8000-0000000000aa::")
    assert r.server == "Linux/4.9 UPnP/1.0 Fire TV/1.0"
    assert r.wakeup == {"mac": "aa:bb:cc:dd:ee:ff", "timeout": "10"}
    assert r.headers["cache-control"] == "max-age=1800"


def test_parse_rejects_garbage_non_200_and_missing_location() -> None:
    assert ssdp.parse_response(b"not http at all", "1.2.3.4") is None
    assert ssdp.parse_response(b"", "1.2.3.4") is None
    assert ssdp.parse_response(b"HTTP/1.1 404 Not Found\r\n\r\n", "1.2.3.4") is None
    assert ssdp.parse_response(b"HTTP/1.1 200 OK\r\nST: x\r\n\r\n", "1.2.3.4") is None
    # NOTIFY is what devices multicast on their own; it is not a reply.
    assert ssdp.parse_response(b"NOTIFY * HTTP/1.1\r\nLOCATION: http://x\r\n\r\n", "1.2.3.4") is None


def test_parse_wakeup_variants() -> None:
    assert ssdp.parse_wakeup("") == {}
    assert ssdp.parse_wakeup("MAC=aa:bb;Timeout=10") == {"mac": "aa:bb", "timeout": "10"}
    assert ssdp.parse_wakeup(" mac = aa:bb ; timeout=10 ; junk") == {"mac": "aa:bb", "timeout": "10"}


def test_build_search_is_a_valid_m_search() -> None:
    message = ssdp.build_search(ssdp.ST_DIAL, 3)
    assert message.startswith(b"M-SEARCH * HTTP/1.1\r\n")
    assert b"HOST: 239.255.255.250:1900\r\n" in message
    assert b'MAN: "ssdp:discover"\r\n' in message
    assert b"MX: 3\r\n" in message
    assert message.endswith(b"ST: urn:dial-multiscreen-org:service:dial:1\r\n\r\n")


class Responder:
    """A UDP peer on localhost that answers every M-SEARCH with canned
    replies, so `search` is tested over a real socket."""

    def __init__(self, replies: list[bytes]):
        self.replies = replies
        self.received: list[bytes] = []
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.settimeout(3)
        self.port = self.sock.getsockname()[1]
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self) -> None:
        try:
            data, peer = self.sock.recvfrom(65535)
        except OSError:
            return
        self.received.append(data)
        for reply in self.replies:
            self.sock.sendto(reply, peer)

    def close(self) -> None:
        self.sock.close()
        self.thread.join(1)


def test_search_collects_and_deduplicates_replies_over_udp() -> None:
    other = DIAL_REPLY.replace(b"0000000000aa", b"0000000000bb").replace(b"178.50", b"178.51")
    responder = Responder([DIAL_REPLY, DIAL_REPLY, b"junk", other])
    try:
        found = ssdp.search(timeout=1.0, target=("127.0.0.1", responder.port), local_address="127.0.0.1")
    finally:
        responder.close()
    assert responder.received and responder.received[0].startswith(b"M-SEARCH")
    assert b"ST: urn:dial-multiscreen-org:service:dial:1" in responder.received[0]
    assert [r.location for r in found] == [
        "http://192.168.178.50:8060/dd.xml",
        "http://192.168.178.51:8060/dd.xml",
    ]
    assert all(r.address == "127.0.0.1" for r in found)


def test_search_returns_empty_on_silence() -> None:
    responder = Responder([])
    try:
        found = ssdp.search(timeout=0.3, target=("127.0.0.1", responder.port), local_address="127.0.0.1")
    finally:
        responder.close()
    assert found == []
