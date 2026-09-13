"""Cast backend against a fake pychromecast. Skipped where pychromecast
is missing."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

pychromecast = pytest.importorskip("pychromecast")

from ytlounge import Screen  # noqa: E402
from yttv.backends import cast as cast_backend  # noqa: E402
from yttv.cache import Device  # noqa: E402

HOST = "192.168.178.69"


class FakeCast:
    def __init__(self, host=HOST, screen_id="screen-from-cast", answer=True):
        self.cast_info = SimpleNamespace(host=host, uuid="uuid-1", friendly_name="Samsung-TV", model_name="U8000F")
        self.calls: list[str] = []
        self.handler = None
        self.screen_id = screen_id
        self.answer = answer

    def wait(self, timeout=None):
        self.calls.append("wait")

    def register_handler(self, handler):
        self.handler = handler
        self.calls.append("register")
        # The controller sends on our namespace; answer like the receiver.
        handler.send_message = self._send  # type: ignore[method-assign]

    def _send(self, data, **kw):
        self.calls.append(f"send:{data['type']}")
        if self.answer:
            self.handler.receive_message(None, {"type": "mdxSessionStatus", "data": {"screenId": self.screen_id}})

    def disconnect(self):
        self.calls.append("disconnect")


class FakeBrowser:
    """Stands in for CastBrowser: announces the fake device on start."""

    instances: list = []

    def __init__(self, listener, zeroconf_instance=None, known_hosts=None):
        self.listener = listener
        self.known_hosts = known_hosts
        self.devices = {}
        self.zc = "zeroconf"
        self.stopped = False
        self.announce: list = []
        FakeBrowser.instances.append(self)

    def start_discovery(self):
        for info in self.announce:
            self.devices[info.uuid] = info
            self.listener.add_cast(info.uuid, "service")

    def stop_discovery(self):
        self.stopped = True


@pytest.fixture
def fake(monkeypatch):
    FakeBrowser.instances = []
    state = SimpleNamespace(cast=FakeCast(), calls=[])

    def get_chromecast_from_cast_info(info, zc, timeout=None, **kw):
        state.calls.append((info, zc, timeout))
        return state.cast

    def make_browser(listener, zeroconf_instance=None, known_hosts=None):
        browser = FakeBrowser(listener, zeroconf_instance, known_hosts)
        browser.announce = [state.cast.cast_info]
        state.browser = browser
        return browser

    monkeypatch.setattr(cast_backend, "CastBrowser", make_browser)
    monkeypatch.setattr(cast_backend.pychromecast, "get_chromecast_from_cast_info", get_chromecast_from_cast_info)
    return state


def test_probe_reports_device_and_disconnects(fake) -> None:
    info = cast_backend.probe(HOST)
    assert info == cast_backend.Info(uuid="uuid-1", friendly_name="Samsung-TV", model="U8000F", host=HOST)
    assert fake.browser.known_hosts == [HOST]
    assert fake.calls == [(fake.cast.cast_info, "zeroconf", cast_backend.CONNECT_TIMEOUT)]
    assert fake.cast.calls == ["wait", "disconnect"]
    assert fake.browser.stopped


def test_probe_no_device(fake, monkeypatch) -> None:
    fake.cast.cast_info.host = "10.0.0.1"
    with pytest.raises(cast_backend.CastError, match="no Cast device"):
        cast_backend.probe(HOST, timeout=0.05)
    assert fake.browser.stopped
    assert fake.calls == [], "nothing is connected when the host never shows up"


def test_screen_id_asks_the_receiver(fake) -> None:
    found, info = cast_backend.screen_id(HOST, timeout=30)
    assert found == "screen-from-cast" and info.friendly_name == "Samsung-TV"
    assert fake.cast.calls == ["wait", "register", "send:getMdxSessionStatus", "disconnect"]
    assert fake.browser.stopped


def test_screen_id_without_answer(fake, monkeypatch) -> None:
    fake.cast.answer = False
    monkeypatch.setattr(cast_backend, "STATUS_TIMEOUT", 0.05)
    with pytest.raises(cast_backend.CastError, match="no screen id"):
        cast_backend.screen_id(HOST, timeout=30)
    assert fake.cast.calls[-1] == "disconnect"


def test_controller_ignores_other_messages() -> None:
    c = cast_backend.MdxController()
    assert c.receive_message(None, {"type": "somethingElse"}) is False
    assert c.receive_message(None, {"type": "mdxSessionStatus", "data": {}}) is True
    assert c.screen_id is None


def test_launcher_fills_in_screen_and_details(fake) -> None:
    d = Device(screen=None, backend="cast", address=HOST)
    cast_backend.CastLauncher().launch(d, timeout=30)
    assert d.screen == Screen("screen-from-cast", "", 0, name="Samsung-TV")
    assert d.backend_data == {"cast_uuid": "uuid-1", "friendly_name": "Samsung-TV"}
    assert d.label == "Samsung-TV"


def test_launcher_keeps_screen_when_id_unchanged(fake) -> None:
    screen = Screen("screen-from-cast", "tok", 4_000_000_000_000, "Samsung-TV")
    d = Device(screen=screen, backend="cast", address=HOST, backend_data={"cast_uuid": "uuid-1"})
    cast_backend.CastLauncher().launch(d, timeout=30)
    assert d.screen is screen


def test_launcher_without_address(fake) -> None:
    with pytest.raises(cast_backend.CastError, match="no address"):
        cast_backend.CastLauncher().launch(Device(screen=None, backend="cast"), timeout=5)


def test_info_drops_pychromecast_placeholder_model(fake) -> None:
    fake.cast.cast_info.model_name = "Unknown model name"
    assert cast_backend.probe(HOST).model == ""
