"""``yttv --doctor``: why does discovery find nothing?

The usual culprit is not the network but the local firewall. An M-SEARCH
goes out to the multicast group, the reply comes back as unicast from the
device's real address. A stateful firewall does not match that reply to
the outgoing packet and drops it as unsolicited. Discovery then reports
"no device found" although the packets are on the wire.

The doctor sends the searches, counts what comes back and, when nothing
does, says so and what to check.
"""

from __future__ import annotations

import socket
from collections.abc import Sequence
from typing import TextIO

from . import ssdp
from .backends import dial


def local_ip_towards(target: str = ssdp.MULTICAST_GROUP) -> str | None:
    """The local address the kernel would use to reach ``target``."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect((target, 1900))
            return sock.getsockname()[0]
    except OSError:
        return None


def subnet_guess(ip: str) -> str:
    """A /24 around ``ip``, good enough for a firewall hint."""
    return ".".join(ip.split(".")[:3]) + ".0/24"


def run(
    out: TextIO,
    *,
    timeout: float = 3.0,
    local_address: str | None = None,
    hosts: Sequence[str] = (),
) -> int:
    """Print the diagnosis. Returns 0 when something answered, 1 otherwise."""
    local_ip = local_address or local_ip_towards()
    print(f"Local address: {local_ip or 'unknown'}", file=out)
    if hosts:
        print(f"Also probing directly: {', '.join(hosts)}", file=out)

    print(f"\nM-SEARCH {ssdp.ST_ALL} ({timeout:.0f} s) ...", file=out)
    everything = ssdp.search(ssdp.ST_ALL, timeout=timeout, local_address=local_address, unicast_hosts=list(hosts))
    by_address: dict[str, ssdp.Response] = {}
    for r in everything:
        by_address.setdefault(r.address, r)
    for address, r in sorted(by_address.items()):
        print(f"  {address:<16} {r.server or '?'}", file=out)
    print(f"  {len(everything)} replies from {len(by_address)} devices", file=out)

    print(f"\nM-SEARCH {ssdp.ST_DIAL} ({timeout:.0f} s) ...", file=out)
    dial_replies = ssdp.search(ssdp.ST_DIAL, timeout=timeout, local_address=local_address, unicast_hosts=list(hosts))
    if not dial_replies:
        print("  no DIAL device answered", file=out)
    with dial._client(local_address) as client:
        for r in dial_replies:
            try:
                description = dial.describe(client, r.location)
            except dial.DialError as exc:
                print(f"  {r.address:<16} description failed: {exc}", file=out)
                continue
            try:
                info = dial.app_info(client, description.application_url)
                app = f"YouTube {info.state}" + (", screen id known" if info.screen_id else "")
            except dial.DialError as exc:
                app = str(exc)
            wake = f", wake-on-lan {r.wakeup['mac']}" if r.wakeup.get("mac") else ""
            print(f"  {r.address:<16} {description.friendly_name}: {app}{wake}", file=out)

    if everything or dial_replies:
        if not dial_replies:
            print(
                "\nSSDP works, but nothing speaks DIAL. Cast TVs (Chromecast, Samsung Tizen) and "
                "Apple TV do not; use --appletv HOST or the cast backend for those.",
                file=out,
            )
        return 0

    subnet = subnet_guess(local_ip) if local_ip else "<your LAN>/24"
    print(
        "\nNothing answered at all. Before blaming the network, check the local firewall:\n"
        "the search goes to 239.255.255.250:1900, but replies arrive as unicast from each\n"
        "device's own address. A stateful firewall (ufw, firewalld) sees no matching\n"
        "connection and drops them as unsolicited, silently.\n"
        "\n"
        "  Quick check:  sudo iptables -vnL | grep -i drop   (before and after --doctor;\n"
        "                a counter that rises is the guilty rule)\n"
        f"  ufw fix:      sudo ufw allow proto udp from {subnet} port 1900\n"
        "  Also try:     yttv --doctor --host <TV ip>   (skips multicast entirely)\n"
        "\n"
        "If a direct probe by --host works while the multicast search does not, the\n"
        "router or Wi-Fi may be blocking multicast between clients.",
        file=out,
    )
    return 1
