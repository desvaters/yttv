from __future__ import annotations

import io

import pytest

from yttv import doctor, ssdp
from yttv.backends import dial


def reply(address: str, st: str = ssdp.ST_ALL, server: str = "FRITZ!Box", **kw) -> ssdp.Response:
    return ssdp.Response(address, f"http://{address}/desc.xml", f"uuid:{address}::{st}", st, server=server, **kw)


@pytest.fixture
def searches(monkeypatch):
    """Script ssdp.search per ST; record the calls."""
    script: dict[str, list[ssdp.Response]] = {ssdp.ST_ALL: [], ssdp.ST_DIAL: []}
    calls: list[dict] = []

    def search(st, *, timeout, local_address, unicast_hosts):
        calls.append({"st": st, "timeout": timeout, "local_address": local_address, "unicast_hosts": unicast_hosts})
        return script[st]

    monkeypatch.setattr(doctor.ssdp, "search", search)
    monkeypatch.setattr(doctor, "local_ip_towards", lambda target=None: "192.168.178.20")
    script["calls"] = calls  # type: ignore[assignment]
    return script


def run(**kw) -> tuple[int, str]:
    out = io.StringIO()
    code = doctor.run(out, **kw)
    return code, out.getvalue()


def test_silence_explains_the_firewall_trap(searches) -> None:
    code, text = run(timeout=2)
    assert code == 1
    assert "0 replies from 0 devices" in text
    assert "no DIAL device answered" in text
    assert "ufw allow proto udp from 192.168.178.0/24 port 1900" in text
    assert "iptables -vnL" in text
    assert "--host" in text
    assert [c["st"] for c in searches["calls"]] == [ssdp.ST_ALL, ssdp.ST_DIAL]
    assert all(c["timeout"] == 2 for c in searches["calls"])


def test_ssdp_without_dial_says_so(searches) -> None:
    searches[ssdp.ST_ALL] = [reply("192.168.178.1"), reply("192.168.178.1", st="upnp:rootdevice"), reply("192.168.178.2", server="Repeater")]
    code, text = run()
    assert code == 0
    assert "3 replies from 2 devices" in text
    assert "192.168.178.1    FRITZ!Box" in text
    assert "nothing speaks DIAL" in text
    assert "ufw allow" not in text


def test_dial_device_is_described_and_app_state_shown(searches, monkeypatch) -> None:
    searches[ssdp.ST_DIAL] = [reply("192.168.178.50", st=ssdp.ST_DIAL, wakeup={"mac": "aa:bb:cc:dd:ee:ff"})]
    monkeypatch.setattr(dial, "describe", lambda client, location: dial.Description("Fire TV", "http://x/apps/"))
    monkeypatch.setattr(dial, "app_info", lambda client, url: dial.AppInfo("running", "screen-1"))
    code, text = run()
    assert code == 0
    assert "192.168.178.50   Fire TV: YouTube running, screen id known, wake-on-lan aa:bb:cc:dd:ee:ff" in text


def test_dial_device_failures_are_reported_per_device(searches, monkeypatch) -> None:
    searches[ssdp.ST_DIAL] = [reply("10.0.0.1", st=ssdp.ST_DIAL), reply("10.0.0.2", st=ssdp.ST_DIAL)]

    def describe(client, location):
        if "10.0.0.1" in location:
            raise dial.DialError("timed out")
        return dial.Description("TV two", "http://x/apps/")

    def app_info(client, url):
        raise dial.DialError("YouTube is not installed on this device")

    monkeypatch.setattr(dial, "describe", describe)
    monkeypatch.setattr(dial, "app_info", app_info)
    code, text = run()
    assert code == 0
    assert "10.0.0.1         description failed: timed out" in text
    assert "10.0.0.2         TV two: YouTube is not installed" in text


def test_hosts_and_interface_are_passed_through(searches) -> None:
    code, text = run(local_address="10.0.0.5", hosts=["10.0.0.9"])
    assert "Local address: 10.0.0.5" in text
    assert "Also probing directly: 10.0.0.9" in text
    assert all(c["local_address"] == "10.0.0.5" and c["unicast_hosts"] == ["10.0.0.9"] for c in searches["calls"])


def test_subnet_guess() -> None:
    assert doctor.subnet_guess("192.168.178.20") == "192.168.178.0/24"
