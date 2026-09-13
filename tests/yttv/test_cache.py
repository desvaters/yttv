from __future__ import annotations

import json
import logging
import stat
from pathlib import Path

import pytest

from ytlounge import Screen
from yttv.cache import Cache, CacheError, Device, default_path, migrate_from_ytcast

FIXTURES = Path(__file__).parent / "fixtures"
YTCAST_FIXTURE = FIXTURES / "ytcast_cache.json"


def make_device(screen_id: str = "s1", **kwargs) -> Device:
    return Device(screen=Screen(screen_id, "tok", 1_800_000_000_000, "TV"), **kwargs)


@pytest.fixture
def cache(tmp_path: Path) -> Cache:
    return Cache(path=tmp_path / "yttv" / "devices.json", ytcast_path=tmp_path / "no-ytcast.json")


# -- migration from ytcast --------------------------------------------------


def test_migration_takes_over_both_kinds_of_entry() -> None:
    devices = migrate_from_ytcast(YTCAST_FIXTURE)
    assert [d.screen.screen_id for d in devices] == ["screen-id-appletv", "screen-id-dial"]

    manual, dial = devices
    assert manual.last_used is True
    assert manual.backend is None and manual.address is None and manual.backend_data == {}
    assert manual.screen == Screen("screen-id-appletv", "TOKEN-APPLETV", 1790258282494, "YouTube on TV")
    assert manual.label == "YouTube on TV"

    assert dial.last_used is False
    assert dial.backend == "dial"
    assert dial.address == "192.168.178.50"
    assert dial.screen.expiration == 1790258282494, "string expiration is normalised"
    assert dial.backend_data == {
        "unique_service_name": "uuid:00000000-0000-4000-8000-0000000000aa::urn:dial-multiscreen-org:service:dial:1",
        "location": "http://192.168.178.50:8060/dd.xml",
        "application_url": "http://192.168.178.50:8060/apps/",
        "friendly_name": "Living room TV",
        "wake_mac": "aa:bb:cc:dd:ee:ff",
        "wake_timeout_s": 10.0,
    }
    assert dial.label == "Living room TV"


def test_migration_skips_broken_entries_with_warning(tmp_path: Path, caplog) -> None:
    path = tmp_path / "ytcast.json"
    good = json.loads(YTCAST_FIXTURE.read_text())[0]
    path.write_text(json.dumps([good, {"Device": None, "Remote": {"ScreenId": "x"}}, "junk", 42]))
    with caplog.at_level(logging.WARNING):
        devices = migrate_from_ytcast(path)
    assert [d.screen.screen_id for d in devices] == ["screen-id-appletv"]
    assert sum("skipping" in r.message for r in caplog.records) == 3


def test_migration_tolerates_unreadable_file(tmp_path: Path, caplog) -> None:
    path = tmp_path / "ytcast.json"
    path.write_text('{"not": "a list"}')
    with caplog.at_level(logging.WARNING):
        assert migrate_from_ytcast(path) == []
    path.write_text("{{{")
    with caplog.at_level(logging.WARNING):
        assert migrate_from_ytcast(path) == []
    assert all("ignoring" in r.message for r in caplog.records)


def test_load_migrates_once_then_uses_own_file(tmp_path: Path) -> None:
    own = tmp_path / "devices.json"
    cache = Cache(path=own, ytcast_path=YTCAST_FIXTURE)

    devices = cache.load()
    assert len(devices) == 2
    assert own.exists(), "migration result is persisted immediately"

    # Change our file, then load again: ytcast's file must not be consulted.
    cache.devices = [make_device("only-ours")]
    cache.save()
    again = Cache(path=own, ytcast_path=YTCAST_FIXTURE).load()
    assert [d.screen.screen_id for d in again] == ["only-ours"]


def test_load_without_any_file_is_empty(cache: Cache) -> None:
    assert cache.load() == []
    assert not cache.path.exists()


# -- own file -------------------------------------------------------------


def test_round_trip_preserves_everything(cache: Cache) -> None:
    cache.devices = [
        make_device("a", last_used=True),
        make_device("b", backend="appletv", address="192.168.178.27", backend_data={"bundle": "x", "n": 1}),
    ]
    cache.save()
    loaded = Cache(path=cache.path, ytcast_path=cache.ytcast_path).load()
    assert loaded == cache.devices


def test_save_is_private_and_leaves_no_temp_file(cache: Cache) -> None:
    cache.devices = [make_device()]
    cache.save()
    mode = stat.S_IMODE(cache.path.stat().st_mode)
    assert mode == 0o600
    assert [p.name for p in cache.path.parent.iterdir()] == ["devices.json"]


def test_file_format_is_versioned_object(cache: Cache) -> None:
    cache.devices = [make_device()]
    cache.save()
    data = json.loads(cache.path.read_text())
    assert data["version"] == 1
    assert isinstance(data["devices"], list)
    assert set(data["devices"][0]) == {"screen", "last_used", "backend", "address", "backend_data"}


@pytest.mark.parametrize(
    "text",
    [
        "[]",  # ytcast's shape, not ours
        '{"version": 99, "devices": []}',
        '{"version": 1}',
        '{"version": 1, "devices": [{"screen": {"screen_id": "x"}}]}',
        "{{{",
    ],
)
def test_load_rejects_unusable_own_file(cache: Cache, text: str) -> None:
    cache.path.parent.mkdir(parents=True)
    cache.path.write_text(text)
    with pytest.raises(CacheError):
        cache.load()


# -- queries and updates ------------------------------------------------------


def test_upsert_replaces_by_screen_id(cache: Cache) -> None:
    cache.upsert(make_device("a"))
    cache.upsert(make_device("b"))
    cache.upsert(make_device("a", address="1.2.3.4"))
    assert [d.screen.screen_id for d in cache.devices] == ["a", "b"]
    assert cache.find("a").address == "1.2.3.4"
    assert cache.find("zzz") is None


def test_set_last_used_is_exclusive(cache: Cache) -> None:
    a, b = make_device("a", last_used=True), make_device("b")
    cache.devices = [a, b]
    cache.set_last_used(b)
    assert cache.last_used() is b
    assert a.last_used is False
    assert cache.last_used() is b


def test_last_used_none_when_nothing_marked(cache: Cache) -> None:
    cache.devices = [make_device("a")]
    assert cache.last_used() is None


def test_default_path_honours_xdg(monkeypatch) -> None:
    monkeypatch.setenv("XDG_CACHE_HOME", "/tmp/xdg-test")
    assert default_path() == Path("/tmp/xdg-test/yttv/devices.json")
    monkeypatch.delenv("XDG_CACHE_HOME")
    assert default_path() == Path("~/.cache/yttv/devices.json").expanduser()


def test_label_falls_back_to_screen_id() -> None:
    d = Device(screen=Screen("only-id", "tok", 1))
    assert d.label == "only-id"


# -- devices without a screen (discovered, never launched) -----------------


def test_screenless_device_round_trips_and_labels_by_friendly_name(cache: Cache) -> None:
    d = Device(screen=None, backend="dial", address="10.0.0.9", backend_data={"friendly_name": "Fire TV", "unique_service_name": "uuid:x"})
    cache.devices = [d]
    cache.save()
    loaded = Cache(path=cache.path, ytcast_path=cache.ytcast_path).load()
    assert loaded == [d]
    assert loaded[0].label == "Fire TV"
    assert json.loads(cache.path.read_text())["devices"][0]["screen"] is None


def test_upsert_and_last_used_match_screenless_devices_by_service(cache: Cache) -> None:
    a = Device(screen=None, backend="dial", backend_data={"unique_service_name": "uuid:a"})
    b = Device(screen=None, backend="dial", backend_data={"unique_service_name": "uuid:b"})
    cache.devices = [a, b]
    cache.upsert(Device(screen=None, backend="dial", address="new", backend_data={"unique_service_name": "uuid:a"}))
    assert [d.backend_data["unique_service_name"] for d in cache.devices] == ["uuid:a", "uuid:b"]
    assert cache.devices[0].address == "new"
    cache.set_last_used(b)
    assert cache.last_used() is b
    assert cache.find_service("uuid:a") is cache.devices[0]
    assert cache.find_service("nope") is None
    assert cache.find("anything") is None


def test_label_without_anything_falls_back_to_address() -> None:
    assert Device(screen=None, address="1.2.3.4").label == "1.2.3.4"
