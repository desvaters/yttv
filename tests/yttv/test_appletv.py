"""Apple TV backend against a fake pyatv. Skipped where pyatv is missing,
which is the point of it being an extra — not a version limit."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

pyatv = pytest.importorskip("pyatv")
from pyatv.const import PowerState, Protocol  # noqa: E402

from ytlounge import Screen  # noqa: E402
from yttv.backends import appletv  # noqa: E402
from yttv.cache import Device  # noqa: E402

HOST = "192.168.178.27"


class FakeService:
    def __init__(self, credentials):
        self.credentials = credentials


class FakeConfig:
    def __init__(self, name="Wohnzimmer", companion=True, credentials="creds"):
        self.name = name
        self._companion = FakeService(credentials) if companion else None

    def get_service(self, protocol):
        return self._companion if protocol == Protocol.Companion else None


class FakeStorage:
    def __init__(self):
        self.loaded = False
        self.saved = False

    async def load(self):
        self.loaded = True

    async def save(self):
        self.saved = True


class FakeATV:
    def __init__(self, power=PowerState.On):
        self.calls: list[str] = []
        self.power = SimpleNamespace(power_state=power, turn_on=self._turn_on)
        self.apps = SimpleNamespace(launch_app=self._launch)

    async def _turn_on(self):
        self.calls.append("turn_on")

    async def _launch(self, bundle):
        self.calls.append(f"launch:{bundle}")

    def close(self):
        self.calls.append("close")


class FakePairing:
    def __init__(self, accept=True):
        self.accept = accept
        self.calls: list[str] = []
        self.has_paired = False

    async def begin(self):
        self.calls.append("begin")

    def pin(self, pin: int):
        self.calls.append(f"pin:{pin}")

    async def finish(self):
        self.calls.append("finish")
        self.has_paired = self.accept

    async def close(self):
        self.calls.append("close")


@pytest.fixture
def fake(monkeypatch):
    """Patch pyatv entry points; returns a namespace to tweak per test."""
    state = SimpleNamespace(
        configs=[FakeConfig()], atv=FakeATV(), storage=FakeStorage(), pairing=FakePairing(),
        scan_calls=[], connect_calls=[], pair_calls=[], slept=[],
    )

    async def scan(loop, hosts=None, timeout=None, storage=None, **kw):
        state.scan_calls.append((hosts, storage))
        return state.configs

    async def connect(config, loop, protocol=None, storage=None):
        state.connect_calls.append((config, protocol))
        return state.atv

    async def pair(config, protocol, loop, storage=None):
        state.pair_calls.append((config, protocol))
        return state.pairing

    async def sleep(seconds):
        state.slept.append(seconds)

    monkeypatch.setattr(appletv.pyatv, "scan", scan)
    monkeypatch.setattr(appletv.pyatv, "connect", connect)
    monkeypatch.setattr(appletv.pyatv, "pair", pair)
    monkeypatch.setattr(appletv.FileStorage, "default_storage", staticmethod(lambda loop: state.storage))
    monkeypatch.setattr(appletv.asyncio, "sleep", sleep)
    return state


def device(address=HOST, **data) -> Device:
    return Device(Screen("s", "t", 1, "TV"), backend="appletv", address=address, backend_data=data)


# -- launch ---------------------------------------------------------------


def test_launch_scans_host_connects_companion_and_launches_youtube(fake) -> None:
    appletv.AppleTVLauncher().launch(device(), timeout=10)
    assert fake.scan_calls == [([HOST], fake.storage)]
    assert fake.storage.loaded
    assert fake.connect_calls == [(fake.configs[0], Protocol.Companion)]
    assert fake.atv.calls == ["launch:com.google.ios.youtube", "close"]
    assert fake.slept == [appletv.DEFAULT_SETTLE_SECONDS]


def test_launch_turns_a_sleeping_tv_on_first(fake) -> None:
    fake.atv = FakeATV(power=PowerState.Off)
    appletv.AppleTVLauncher().launch(device(), timeout=10)
    assert fake.atv.calls == ["turn_on", "launch:com.google.ios.youtube", "close"]


def test_launch_settle_is_per_device(fake) -> None:
    appletv.AppleTVLauncher().launch(device(settle_seconds=0.5), timeout=10)
    assert fake.slept == [0.5]


def test_launch_closes_even_when_the_app_launch_fails(fake) -> None:
    async def boom(bundle):
        raise RuntimeError("Companion said no")

    fake.atv.apps.launch_app = boom
    with pytest.raises(RuntimeError, match="Companion said no"):
        appletv.AppleTVLauncher().launch(device(), timeout=10)
    assert fake.atv.calls == ["close"]


def test_launch_without_address_or_device_or_pairing(fake) -> None:
    with pytest.raises(appletv.AppleTVError, match="no address"):
        appletv.AppleTVLauncher().launch(device(address=None), timeout=10)

    fake.configs = []
    with pytest.raises(appletv.AppleTVError, match="no Apple TV answered"):
        appletv.AppleTVLauncher().launch(device(), timeout=10)

    fake.configs = [FakeConfig(credentials="")]
    with pytest.raises(appletv.AppleTVError, match="not paired"):
        appletv.AppleTVLauncher().launch(device(), timeout=10)
    assert fake.connect_calls == []


def test_launch_times_out(fake, monkeypatch) -> None:
    async def hang(config, loop, protocol=None, storage=None):
        await asyncio.Event().wait()

    monkeypatch.setattr(appletv.pyatv, "connect", hang)
    with pytest.raises(appletv.AppleTVError, match="gave up"):
        appletv.AppleTVLauncher().launch(device(), timeout=0.2)


def test_launch_works_from_inside_a_running_event_loop(fake) -> None:
    """A web app calling cast() from a coroutine must not trip asyncio.run."""

    async def caller():
        appletv.AppleTVLauncher().launch(device(), timeout=10)

    asyncio.run(caller())
    assert fake.atv.calls[0] == "launch:com.google.ios.youtube"


# -- pairing --------------------------------------------------------------


def test_ensure_paired_is_a_no_op_when_already_paired(fake) -> None:
    asked: list[str] = []
    name = appletv.ensure_paired(HOST, lambda n: asked.append(n) or "1234")
    assert name == "Wohnzimmer"
    assert asked == [] and fake.pair_calls == []


def test_ensure_paired_runs_the_pin_flow_and_saves(fake) -> None:
    fake.configs = [FakeConfig(credentials="")]
    name = appletv.ensure_paired(HOST, lambda n: " 4321 ")
    assert name == "Wohnzimmer"
    assert fake.pair_calls == [(fake.configs[0], Protocol.Companion)]
    assert fake.pairing.calls == ["begin", "pin:4321", "finish", "close"]
    assert fake.storage.saved


def test_ensure_paired_rejects_bad_pin_and_refusal(fake) -> None:
    fake.configs = [FakeConfig(credentials="")]
    with pytest.raises(appletv.AppleTVError, match="digits"):
        appletv.ensure_paired(HOST, lambda n: "abcd")
    assert fake.pairing.calls == ["begin", "close"], "closed even on a bad PIN"

    fake.pairing = FakePairing(accept=False)
    with pytest.raises(appletv.AppleTVError, match="not accepted"):
        appletv.ensure_paired(HOST, lambda n: "1234")
    assert not fake.storage.saved


def test_ensure_paired_without_companion(fake) -> None:
    fake.configs = [FakeConfig(companion=False)]
    with pytest.raises(appletv.AppleTVError, match="does not offer Companion"):
        appletv.ensure_paired(HOST, lambda n: "1234")
