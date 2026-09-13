"""The library API, with the Lounge mocked at the HTTP layer and the cache
in a temp dir. Device selection, token refresh and error mapping are what
matter here; the wire format is covered in tests/ytlounge."""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest

import yttv
from ytlounge import Lounge, Screen
from yttv.backends import register, _LAUNCHERS
from yttv.cache import Cache, Device

FIXTURES = Path(__file__).parent.parent / "ytlounge" / "fixtures"
SESSION_BODY = (FIXTURES / "bind_session.txt").read_text()
ACK_BODY = (FIXTURES / "bind_command.txt").read_text()
FAR_FUTURE = 4_000_000_000_000
ID1, ID2 = "dQw4w9WgXcQ", "jNQXAC9IVRw"


class FakeLoungeServer:
    """Answers bind/pair/refresh like the real endpoint, records requests."""

    def __init__(self, *, bind_status: int = 200, fail_first_bind: bool = False):
        self.requests: list[httpx.Request] = []
        self.bind_status = bind_status
        self.fail_first_bind = fail_first_bind
        self.binds = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if path.endswith("/bc/bind"):
            if "SID" in request.url.params:
                return httpx.Response(200, text=ACK_BODY)
            self.binds += 1
            if self.fail_first_bind and self.binds == 1:
                return httpx.Response(401, text="expired")
            return httpx.Response(self.bind_status, text=SESSION_BODY)
        if path.endswith("/get_lounge_token_batch"):
            sid = parse_qs(request.content.decode())["screen_ids"][0]
            return httpx.Response(
                200,
                json={"screens": [{"screenId": sid, "loungeToken": "REFRESHED", "expiration": FAR_FUTURE}]},
            )
        if path.endswith("/get_screen"):
            code = parse_qs(request.content.decode())["pairing_code"][0]
            if code == "000000":
                return httpx.Response(404, text="")
            return httpx.Response(
                200,
                json={"screen": {"screenId": f"screen-{code}", "loungeToken": "PAIRED", "expiration": str(FAR_FUTURE), "name": "New TV"}},
            )
        return httpx.Response(500, text="unexpected")

    def commands(self) -> list[dict[str, str]]:
        out = []
        for r in self.requests:
            if r.url.path.endswith("/bc/bind") and "SID" in r.url.params:
                out.append({k: v[0] for k, v in parse_qs(r.content.decode()).items()})
        return out


@pytest.fixture
def server() -> FakeLoungeServer:
    return FakeLoungeServer()


@pytest.fixture
def lounge(server: FakeLoungeServer) -> Lounge:
    client = httpx.Client(transport=httpx.MockTransport(server))
    return Lounge(name="test", client=client, sleep=lambda s: None, rng=lambda a, b: a)


@pytest.fixture
def cache(tmp_path: Path) -> Cache:
    c = Cache(path=tmp_path / "devices.json", ytcast_path=tmp_path / "none.json")
    c.devices = [
        Device(Screen("screen-a", "TOKEN-A", FAR_FUTURE, "Living room"), last_used=True),
        Device(Screen("screen-b", "TOKEN-B", FAR_FUTURE, "Bedroom"), address="192.168.1.9"),
    ]
    c.save()
    return c


@pytest.fixture(autouse=True)
def clean_launchers():
    saved = dict(_LAUNCHERS)
    _LAUNCHERS.clear()
    yield
    _LAUNCHERS.clear()
    _LAUNCHERS.update(saved)


# -- devices --------------------------------------------------------------


def test_devices_reads_cache_without_network(cache: Cache, server: FakeLoungeServer) -> None:
    found = yttv.devices(cache=Cache(path=cache.path, ytcast_path=cache.ytcast_path))
    assert [d.label for d in found] == ["Living room", "Bedroom"]
    assert server.requests == []


def test_devices_empty_without_cache(tmp_path: Path) -> None:
    assert yttv.devices(cache=Cache(path=tmp_path / "x.json", ytcast_path=tmp_path / "y.json")) == []


# -- cast -----------------------------------------------------------------


def test_cast_plays_on_last_used_and_accepts_urls(cache: Cache, lounge: Lounge, server: FakeLoungeServer) -> None:
    used = yttv.cast([f"https://youtu.be/{ID1}?t=1m", ID2], cache=cache, lounge=lounge)
    assert used.label == "Living room"
    assert server.commands() == [
        {"count": "1", "req0__sc": "setPlaylist", "req0_currentIndex": "0", "req0_currentTime": "60", "req0_videoId": ID1, "req0_videoIds": f"{ID1},{ID2}"}
    ]
    assert server.requests[0].url.params["loungeIdToken"] == "TOKEN-A"


def test_cast_queue_appends_each_video(cache: Cache, lounge: Lounge, server: FakeLoungeServer) -> None:
    yttv.cast([ID1, ID2], queue=True, cache=cache, lounge=lounge)
    assert [c["req0__sc"] for c in server.commands()] == ["addVideo", "addVideo"]
    assert [c["req0_videoId"] for c in server.commands()] == [ID1, ID2]


def test_cast_selects_device_by_name_or_address_and_marks_last_used(cache: Cache, lounge: Lounge, server: FakeLoungeServer) -> None:
    used = yttv.cast([ID1], device="bed", cache=cache, lounge=lounge)
    assert used.screen.screen_id == "screen-b"
    assert server.requests[0].url.params["loungeIdToken"] == "TOKEN-B"

    reloaded = Cache(path=cache.path, ytcast_path=cache.ytcast_path)
    reloaded.load()
    assert reloaded.last_used().screen.screen_id == "screen-b"
    assert [d.last_used for d in reloaded.devices] == [False, True]

    used = yttv.cast([ID1], device="192.168.1.9", cache=cache, lounge=lounge)
    assert used.screen.screen_id == "screen-b"


def test_cast_device_selection_errors(cache: Cache, lounge: Lounge) -> None:
    with pytest.raises(yttv.NoDeviceError, match="matches"):
        yttv.cast([ID1], device="kitchen", cache=cache, lounge=lounge)
    with pytest.raises(yttv.NoDeviceError, match="ambiguous"):
        yttv.cast([ID1], device="room", cache=cache, lounge=lounge)


def test_cast_without_last_used_uses_the_only_device_else_fails(tmp_path: Path, lounge: Lounge) -> None:
    c = Cache(path=tmp_path / "d.json", ytcast_path=tmp_path / "none.json")
    c.devices = [Device(Screen("only", "T", FAR_FUTURE, "Only TV"))]
    c.save()
    assert yttv.cast([ID1], cache=c, lounge=lounge).label == "Only TV"

    c.devices = []
    c.save()
    with pytest.raises(yttv.NoDeviceError, match="Pair one"):
        yttv.cast([ID1], cache=c, lounge=lounge)


def test_cast_rejects_bad_videos_before_touching_anything(cache: Cache, lounge: Lounge, server: FakeLoungeServer) -> None:
    with pytest.raises(yttv.InvalidVideoError, match="No video"):
        yttv.cast([], cache=cache, lounge=lounge)
    with pytest.raises(yttv.InvalidVideoError, match="nonsense"):
        yttv.cast([ID1, "nonsense"], cache=cache, lounge=lounge)
    assert server.requests == []


def test_cast_refreshes_expiring_token_and_persists_it(cache: Cache, lounge: Lounge, server: FakeLoungeServer) -> None:
    cache.devices[0].screen = Screen("screen-a", "OLD", expiration=1, name="Living room")
    cache.save()
    yttv.cast([ID1], cache=cache, lounge=lounge)

    assert server.requests[0].url.path.endswith("/get_lounge_token_batch")
    assert server.requests[1].url.params["loungeIdToken"] == "REFRESHED"
    reloaded = Cache(path=cache.path, ytcast_path=cache.ytcast_path)
    reloaded.load()
    assert reloaded.find("screen-a").screen.lounge_token == "REFRESHED"


def test_cast_retries_once_with_fresh_token_when_session_fails(cache: Cache) -> None:
    server = FakeLoungeServer(fail_first_bind=True)
    lounge = Lounge(client=httpx.Client(transport=httpx.MockTransport(server)), sleep=lambda s: None)
    yttv.cast([ID1], cache=cache, lounge=lounge)
    paths = [r.url.path.rsplit("/", 1)[1] for r in server.requests]
    assert paths == ["bind", "get_lounge_token_batch", "bind", "bind"]
    assert server.requests[-1].url.params["loungeIdToken"] == "REFRESHED"


def test_cast_reports_persistent_failure_as_cast_error(cache: Cache) -> None:
    server = FakeLoungeServer(bind_status=500)
    lounge = Lounge(client=httpx.Client(transport=httpx.MockTransport(server)), sleep=lambda s: None)
    with pytest.raises(yttv.CastError, match="Living room"):
        yttv.cast([ID1], cache=cache, lounge=lounge)


def test_cast_runs_backend_launcher_before_sending(cache: Cache, lounge: Lounge, server: FakeLoungeServer) -> None:
    calls: list[tuple[str, float]] = []

    class Launcher:
        def launch(self, device: Device, *, timeout: float) -> None:
            calls.append((device.label, timeout))
            assert server.requests == [], "launch happens before any Lounge request"

    register("fake", Launcher())
    cache.devices[0].backend = "fake"
    cache.save()
    yttv.cast([ID1], cache=cache, lounge=lounge, timeout=42)
    assert calls == [("Living room", 42)]


def test_cast_wraps_launcher_failure(cache: Cache, lounge: Lounge) -> None:
    class Launcher:
        def launch(self, device: Device, *, timeout: float) -> None:
            raise OSError("no route to host")

    register("fake", Launcher())
    cache.devices[0].backend = "fake"
    cache.save()
    with pytest.raises(yttv.CastError, match="no route to host"):
        yttv.cast([ID1], cache=cache, lounge=lounge)


def test_cast_without_backend_skips_launch(cache: Cache, lounge: Lounge) -> None:
    cache.devices[0].backend = "unknown-backend"
    yttv.cast([ID1], cache=cache, lounge=lounge)  # no error: unknown means "app must be open"


# -- pair -----------------------------------------------------------------


def test_pair_adds_device_and_makes_it_last_used(cache: Cache, lounge: Lounge, server: FakeLoungeServer) -> None:
    device = yttv.pair("123 456-789", cache=cache, lounge=lounge)
    assert device.screen == Screen("screen-123456789", "PAIRED", FAR_FUTURE, "New TV")
    assert parse_qs(server.requests[0].content.decode()) == {"pairing_code": ["123456789"]}

    reloaded = Cache(path=cache.path, ytcast_path=cache.ytcast_path)
    reloaded.load()
    assert [d.screen.screen_id for d in reloaded.devices] == ["screen-a", "screen-b", "screen-123456789"]
    assert reloaded.last_used().screen.screen_id == "screen-123456789"


def test_pair_same_screen_again_updates_in_place(cache: Cache, lounge: Lounge) -> None:
    cache.devices[0].screen = Screen("screen-111111", "OLD", 1, "Living room")
    cache.devices[0].address = "10.0.0.5"
    cache.save()
    device = yttv.pair("111111", cache=cache, lounge=lounge)
    assert device.address == "10.0.0.5", "backend details survive re-pairing"
    assert device.screen.lounge_token == "PAIRED"
    assert len(cache.devices) == 2


def test_pair_rejects_bad_code_without_network(cache: Cache, lounge: Lounge, server: FakeLoungeServer) -> None:
    for code in ["", "12345", "1" * 21, "abc"]:
        with pytest.raises(yttv.PairError, match="6 to 20 digits"):
            yttv.pair(code, cache=cache, lounge=lounge)
    assert server.requests == []


def test_pair_reports_rejected_code(cache: Cache, lounge: Lounge) -> None:
    with pytest.raises(yttv.PairError, match="did not accept"):
        yttv.pair("000000", cache=cache, lounge=lounge)


def test_errors_share_a_base_class() -> None:
    for cls in (yttv.NoDeviceError, yttv.InvalidVideoError, yttv.CastError, yttv.PairError):
        assert issubclass(cls, yttv.YttvError)


# -- attach ---------------------------------------------------------------


def test_attach_records_backend_and_address(cache: Cache) -> None:
    device = yttv.attach("bed", "appletv", "192.168.178.27", cache=cache, apple_name="Wohnzimmer")
    assert device.screen.screen_id == "screen-b"
    reloaded = Cache(path=cache.path, ytcast_path=cache.ytcast_path)
    reloaded.load()
    found = reloaded.find("screen-b")
    assert (found.backend, found.address) == ("appletv", "192.168.178.27")
    assert found.backend_data == {"apple_name": "Wohnzimmer"}


def test_attach_defaults_to_last_used_and_rejects_unknown_backend(cache: Cache) -> None:
    assert yttv.attach(None, "dial", "10.0.0.1", cache=cache).screen.screen_id == "screen-a"
    with pytest.raises(yttv.YttvError, match="Unknown backend"):
        yttv.attach(None, "toaster", "10.0.0.1", cache=cache)


def test_cast_reports_missing_backend_dependency(cache: Cache, lounge: Lounge, monkeypatch) -> None:
    from yttv import api as api_module
    from yttv.backends import BackendUnavailable

    def unavailable(name):
        raise BackendUnavailable("needs pyatv")

    monkeypatch.setattr(api_module, "get_launcher", unavailable)
    cache.devices[0].backend = "appletv"
    with pytest.raises(yttv.CastError, match="needs pyatv"):
        yttv.cast([ID1], cache=cache, lounge=lounge)


# -- discover / screenless devices -------------------------------------------


def test_discover_merges_into_cache_keeping_known_screens(cache: Cache, monkeypatch) -> None:
    from yttv.backends import dial

    known = Device(Screen("screen-d", "TOK", FAR_FUTURE, "Living room TV"), backend="dial", address="10.0.0.1",
                   backend_data={"unique_service_name": "uuid:d", "application_url": "http://old/apps/"})
    cache.devices.append(known)
    cache.save()
    fresh = [
        Device(None, backend="dial", address="10.0.0.2", backend_data={"unique_service_name": "uuid:d", "application_url": "http://new/apps/", "friendly_name": "Living room TV"}),
        Device(None, backend="dial", address="10.0.0.3", backend_data={"unique_service_name": "uuid:e", "friendly_name": "Other TV"}),
    ]
    monkeypatch.setattr(dial, "discover", lambda **kw: fresh)

    found = yttv.discover(cache=cache)
    assert [d.label for d in found] == ["Living room TV", "Other TV"]
    merged = found[0]
    assert merged.screen == known.screen, "screen and token survive a re-discovery"
    assert merged.address == "10.0.0.2" and merged.backend_data["application_url"] == "http://new/apps/"

    reloaded = Cache(path=cache.path, ytcast_path=cache.ytcast_path)
    reloaded.load()
    assert len(reloaded.devices) == 4


def test_cast_to_screenless_device_takes_screen_from_launcher(cache: Cache, lounge: Lounge, server: FakeLoungeServer) -> None:
    class Launcher:
        def launch(self, device: Device, *, timeout: float) -> None:
            device.screen = Screen("screen-new", "", 0, "Fire TV")

    register("fake", Launcher())
    cache.devices = [Device(None, backend="fake", last_used=True, backend_data={"friendly_name": "Fire TV"})]
    cache.save()
    used = yttv.cast([ID1], cache=cache, lounge=lounge)
    paths = [r.url.path.rsplit("/", 1)[1] for r in server.requests]
    assert paths == ["get_lounge_token_batch", "bind", "bind"], "empty token is refreshed before use"
    assert used.screen.lounge_token == "REFRESHED"
    reloaded = Cache(path=cache.path, ytcast_path=cache.ytcast_path)
    reloaded.load()
    assert reloaded.devices[0].screen.screen_id == "screen-new"


def test_cast_to_screenless_device_without_launcher_explains(cache: Cache, lounge: Lounge) -> None:
    cache.devices = [Device(None, last_used=True, backend_data={"friendly_name": "Mystery"})]
    cache.save()
    with pytest.raises(yttv.CastError, match="screen id yet"):
        yttv.cast([ID1], cache=cache, lounge=lounge)


# -- add_device -------------------------------------------------------------


def test_add_device_creates_screenless_entry_and_updates_by_identity(cache: Cache) -> None:
    d = yttv.add_device("cast", "192.168.178.69", cache=cache, cast_uuid="u1", friendly_name="Samsung-TV")
    assert d.screen is None and d.backend == "cast" and d.label == "Samsung-TV"
    assert len(cache.devices) == 3

    d.screen = Screen("s-cast", "tok", FAR_FUTURE, "Samsung-TV")
    cache.save()
    again = yttv.add_device("cast", "192.168.178.70", cache=cache, cast_uuid="u1", model="U8000F")
    assert again.address == "192.168.178.70" and again.screen.screen_id == "s-cast"
    assert again.backend_data["model"] == "U8000F"
    assert len(cache.devices) == 3
    with pytest.raises(yttv.YttvError, match="Unknown backend"):
        yttv.add_device("toaster", "1.2.3.4", cache=cache)
