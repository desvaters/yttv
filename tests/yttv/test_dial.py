from __future__ import annotations

import socket
from pathlib import Path

import httpx
import pytest

from ytlounge import Screen
from yttv import ssdp
from yttv.backends import dial
from yttv.cache import Device

FIXTURES = Path(__file__).parent / "fixtures"
DESCRIPTION = (FIXTURES / "dial_description.xml").read_text()
RUNNING = (FIXTURES / "dial_app_running.xml").read_text()
STOPPED = (FIXTURES / "dial_app_stopped.xml").read_text()
APP_URL = "http://192.168.178.50:8060/apps/"
LOCATION = "http://192.168.178.50:8060/dd.xml"


class FakeTV:
    """A DIAL device behind httpx.MockTransport with a scriptable state."""

    def __init__(self, state: str = "stopped", installed: bool = True, reachable: bool = True):
        self.state = state
        self.installed = installed
        self.reachable = reachable
        self.requests: list[tuple[str, str]] = []
        self.launches = 0
        self.info_reads_until_running = 0  # after a launch, how many polls stay "stopped"

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append((request.method, str(request.url)))
        if not self.reachable:
            raise httpx.ConnectError("no route to host", request=request)
        if str(request.url) == LOCATION:
            return httpx.Response(200, text=DESCRIPTION, headers={"Application-URL": APP_URL})
        if str(request.url) == APP_URL + "YouTube":
            if not self.installed:
                return httpx.Response(404)
            if request.method == "POST":
                self.launches += 1
                already = self.state == "running"
                self.state = "running"
                return httpx.Response(200 if already else 201, headers={"Location": APP_URL + "YouTube/run"})
            if self.launches and self.info_reads_until_running > 0:
                self.info_reads_until_running -= 1
                return httpx.Response(200, text=STOPPED)
            return httpx.Response(200, text=RUNNING if self.state == "running" else STOPPED)
        return httpx.Response(404)

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self))


@pytest.fixture
def tv(monkeypatch) -> FakeTV:
    fake = FakeTV()
    monkeypatch.setattr(dial, "_client", lambda local_address=None: fake.client())
    return fake


def device(screen: Screen | None = None, **data) -> Device:
    base = {"application_url": APP_URL, "friendly_name": "Living room TV"}
    base.update(data)
    return Device(screen=screen, backend="dial", address="192.168.178.50", backend_data=base)


# -- REST ---------------------------------------------------------------------


def test_app_url_joins_with_or_without_slash() -> None:
    assert dial.app_url("http://h:1/apps/") == "http://h:1/apps/YouTube"
    assert dial.app_url("http://h:1/apps") == "http://h:1/apps/YouTube"


def test_describe_reads_header_and_friendly_name(tv: FakeTV) -> None:
    with tv.client() as client:
        d = dial.describe(client, LOCATION)
    assert d == dial.Description(friendly_name="Living room TV", application_url=APP_URL)


def test_describe_rejects_non_dial_and_bad_xml() -> None:
    def handler(request):
        if "nodial" in str(request.url):
            return httpx.Response(200, text=DESCRIPTION)
        return httpx.Response(200, text="<not xml", headers={"Application-URL": APP_URL})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(dial.DialError, match="no Application-URL"):
            dial.describe(client, "http://x/nodial")
        with pytest.raises(dial.DialError, match="bad XML"):
            dial.describe(client, "http://x/bad")


def test_app_info_states(tv: FakeTV) -> None:
    with tv.client() as client:
        stopped = dial.app_info(client, APP_URL)
        assert (stopped.state, stopped.screen_id, stopped.running) == ("stopped", None, False)
        tv.state = "running"
        running = dial.app_info(client, APP_URL)
        assert (running.state, running.screen_id, running.running) == ("running", "screen-id-from-dial", True)
        tv.installed = False
        with pytest.raises(dial.DialError, match="not installed"):
            dial.app_info(client, APP_URL)


def test_launch_app_posts_with_content_type(tv: FakeTV) -> None:
    with tv.client() as client:
        dial.launch_app(client, APP_URL)
        dial.launch_app(client, APP_URL)  # 200 when already running is fine too
    assert tv.launches == 2
    assert [m for m, _ in tv.requests] == ["POST", "POST"]


# -- Wake-on-LAN -------------------------------------------------------------


def test_magic_packet_layout() -> None:
    packet = dial.magic_packet("aa:bb:cc:dd:ee:ff")
    assert packet[:6] == b"\xff" * 6
    assert packet[6:] == bytes.fromhex("aabbccddeeff") * 16
    assert dial.magic_packet("AA-BB-CC-DD-EE-FF") == packet
    with pytest.raises(ValueError):
        dial.magic_packet("aa:bb")


def test_wake_sends_broadcast_udp() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sink:
        sink.bind(("127.0.0.1", 0))
        sink.settimeout(2)
        port = sink.getsockname()[1]
        dial.wake("aa:bb:cc:dd:ee:ff", broadcast="127.0.0.1", port=port)
        data, _ = sink.recvfrom(1024)
    assert data == dial.magic_packet("aa:bb:cc:dd:ee:ff")


# -- launcher ---------------------------------------------------------------


class Clock:
    def __init__(self):
        self.now = 0.0
        self.sleeps: list[float] = []

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds

    def monotonic(self) -> float:
        return self.now


@pytest.fixture
def launcher(tv: FakeTV):
    clock = Clock()
    woken: list[str] = []
    launcher = dial.DialLauncher(sleep=clock.sleep, clock=clock.monotonic, wake=woken.append)
    launcher.clock, launcher.woken = clock, woken  # type: ignore[attr-defined]
    return launcher


def test_launch_starts_stopped_app_and_learns_screen_id(tv: FakeTV, launcher) -> None:
    tv.info_reads_until_running = 0
    d = device()
    launcher.launch(d, timeout=30)
    assert tv.launches == 1
    assert d.screen == Screen("screen-id-from-dial", "", 0, name="Living room TV")
    assert launcher.clock.sleeps == [dial.POLL_INTERVAL, dial.DEFAULT_SETTLE_SECONDS]
    assert launcher.woken == []


def test_launch_polls_until_running(tv: FakeTV, launcher) -> None:
    tv.info_reads_until_running = 3  # the first three polls after launch still say stopped
    launcher.launch(device(), timeout=30)
    polls = [m for m, url in tv.requests if m == "GET" and url.endswith("YouTube")]
    assert len(polls) == 1 + 4  # one before launch, then polls until running


def test_launch_skips_launch_when_already_running(tv: FakeTV, launcher) -> None:
    tv.state = "running"
    d = device(Screen("old-screen", "tok", 1, "Living room TV"))
    launcher.launch(d, timeout=30)
    assert tv.launches == 0
    assert d.screen.screen_id == "screen-id-from-dial", "a changed screen id is taken over"
    assert launcher.clock.sleeps == [], "no settle when nothing was started"


def test_launch_keeps_screen_object_when_id_unchanged(tv: FakeTV, launcher) -> None:
    tv.state = "running"
    screen = Screen("screen-id-from-dial", "tok", 4_000_000_000_000, "Living room TV")
    d = device(screen)
    launcher.launch(d, timeout=30)
    assert d.screen is screen, "token must survive when the id is the same"


def test_launch_wakes_a_silent_device_then_starts_the_app(tv: FakeTV, launcher) -> None:
    tv.reachable = False
    d = device(wake_mac="aa:bb:cc:dd:ee:ff")

    real_sleep = launcher.clock.sleep

    def sleep_and_wake(seconds: float) -> None:
        real_sleep(seconds)
        if launcher.clock.now >= 2:
            tv.reachable = True

    launcher._sleep = sleep_and_wake
    launcher.launch(d, timeout=60)
    assert launcher.woken == ["aa:bb:cc:dd:ee:ff"]
    assert tv.launches == 1
    assert d.screen is not None


def test_launch_gives_up_when_device_never_wakes(tv: FakeTV, launcher) -> None:
    tv.reachable = False
    with pytest.raises(dial.DialError, match="did not wake up"):
        launcher.launch(device(wake_mac="aa:bb:cc:dd:ee:ff"), timeout=5)
    assert launcher.woken == ["aa:bb:cc:dd:ee:ff"]


def test_launch_without_mac_reports_unreachable(tv: FakeTV, launcher) -> None:
    tv.reachable = False
    with pytest.raises(dial.DialError, match="no route"):
        launcher.launch(device(), timeout=5)
    assert launcher.woken == []


def test_launch_not_installed_does_not_wake(tv: FakeTV, launcher) -> None:
    tv.installed = False
    with pytest.raises(dial.DialError, match="not installed"):
        launcher.launch(device(wake_mac="aa:bb:cc:dd:ee:ff"), timeout=5)
    assert launcher.woken == []


def test_launch_times_out_when_app_never_runs(tv: FakeTV, launcher) -> None:
    tv.info_reads_until_running = 100
    with pytest.raises(dial.DialError, match="did not come up"):
        launcher.launch(device(), timeout=5)


def test_launch_needs_application_url(launcher) -> None:
    d = Device(screen=None, backend="dial", address="1.2.3.4")
    with pytest.raises(dial.DialError, match="application URL"):
        launcher.launch(d, timeout=5)


# -- discovery ----------------------------------------------------------------


def test_discover_describes_every_reply_in_parallel(tv: FakeTV, monkeypatch) -> None:
    replies = [
        ssdp.Response("192.168.178.50", LOCATION, "uuid:aa::dial", ssdp.ST_DIAL, wakeup={"mac": "aa:bb:cc:dd:ee:ff", "timeout": "10"}),
        ssdp.Response("192.168.178.51", "http://192.168.178.51/broken", "uuid:bb::dial", ssdp.ST_DIAL),
    ]
    seen: dict = {}

    def search(st, *, timeout, local_address, unicast_hosts):
        seen.update(st=st, timeout=timeout, local_address=local_address, unicast_hosts=unicast_hosts)
        return replies

    monkeypatch.setattr(dial.ssdp, "search", search)
    found = dial.discover(timeout=2, local_address="10.0.0.5", unicast_hosts=["192.168.178.9"])

    assert seen == {"st": ssdp.ST_DIAL, "timeout": 2, "local_address": "10.0.0.5", "unicast_hosts": ["192.168.178.9"]}
    assert len(found) == 1, "the undescribable one is skipped"
    d = found[0]
    assert d.screen is None and d.backend == "dial" and d.address == "192.168.178.50"
    assert d.backend_data == {
        "unique_service_name": "uuid:aa::dial",
        "location": LOCATION,
        "application_url": APP_URL,
        "friendly_name": "Living room TV",
        "wake_mac": "aa:bb:cc:dd:ee:ff",
        "wake_timeout_s": 10.0,
    }
    assert d.label == "Living room TV"


def test_discover_nothing(monkeypatch) -> None:
    monkeypatch.setattr(dial.ssdp, "search", lambda *a, **k: [])
    assert dial.discover() == []
