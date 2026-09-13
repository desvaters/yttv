"""The CLI is a thin layer over yttv.api; the api is stubbed here."""

from __future__ import annotations

import io
import logging

import pytest

from ytlounge import Screen
from yttv import api, cli
from yttv.cache import Device

FAR_FUTURE = 4_000_000_000_000
LIVING = Device(Screen("screen-a", "T", FAR_FUTURE, "Living room"), last_used=True)
BEDROOM = Device(Screen("screen-b", "T", 1, "Bedroom"), backend="appletv", address="192.168.1.9")


class Stub:
    def __init__(self, devices=(), fail: Exception | None = None):
        self._devices = list(devices)
        self.calls: list[tuple] = []
        self.fail = fail

    def devices(self):
        self.calls.append(("devices",))
        return list(self._devices)

    def cast(self, videos, *, queue=False, device=None, **kwargs):
        self.calls.append(("cast", list(videos), queue, device))
        if self.fail:
            raise self.fail
        return LIVING

    def pair(self, code, **kwargs):
        self.calls.append(("pair", code))
        if self.fail:
            raise self.fail
        return BEDROOM


@pytest.fixture
def stub(monkeypatch) -> Stub:
    s = Stub(devices=[LIVING, BEDROOM])
    monkeypatch.setattr(cli.api, "devices", s.devices)
    monkeypatch.setattr(cli.api, "cast", s.cast)
    monkeypatch.setattr(cli.api, "pair", s.pair)
    return s


def run(argv: list[str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = cli.run(argv, out=out, err=err)
    return code, out.getvalue(), err.getvalue()


def test_no_arguments_is_a_usage_error(stub: Stub) -> None:
    code, out, err = run([])
    assert code == 2
    assert "usage:" in err and "--pair" in err
    assert stub.calls == []


def test_list_shows_devices(stub: Stub) -> None:
    code, out, _ = run(["-l"])
    assert code == 0
    lines = out.splitlines()
    assert lines[0].startswith("* Living room") and "paired by code" in lines[0] and "token until" in lines[0]
    assert lines[1].startswith("  Bedroom") and "192.168.1.9 (appletv)" in lines[1] and "token expired" in lines[1]
    assert stub.calls == [("devices",)]


def test_list_without_devices_hints_at_pairing(monkeypatch) -> None:
    monkeypatch.setattr(cli.api, "devices", lambda: [])
    code, out, _ = run(["--list"])
    assert code == 0 and "--pair" in out


def test_play_videos_on_default_device(stub: Stub) -> None:
    code, out, _ = run(["dQw4w9WgXcQ", "https://youtu.be/jNQXAC9IVRw"])
    assert code == 0
    assert out == "Playing 2 videos on Living room.\n"
    assert stub.calls == [("cast", ["dQw4w9WgXcQ", "https://youtu.be/jNQXAC9IVRw"], False, None)]


def test_add_to_queue_on_named_device(stub: Stub) -> None:
    code, out, _ = run(["-a", "-d", "bed", "dQw4w9WgXcQ"])
    assert code == 0
    assert out == "Added 1 video to the queue on Living room.\n"
    assert stub.calls == [("cast", ["dQw4w9WgXcQ"], True, "bed")]


def test_pair_alone(stub: Stub) -> None:
    code, out, _ = run(["--pair", "123 456 789"])
    assert code == 0
    assert out == "Paired with Bedroom.\n"
    assert stub.calls == [("pair", "123 456 789")]


def test_pair_then_play_uses_the_new_device(stub: Stub) -> None:
    code, out, _ = run(["--pair", "123456789", "dQw4w9WgXcQ"])
    assert code == 0
    assert out == "Paired with Bedroom.\nPlaying 1 video on Living room.\n"
    assert stub.calls[1] == ("cast", ["dQw4w9WgXcQ"], False, BEDROOM)


def test_pair_has_no_short_option(stub: Stub) -> None:
    with pytest.raises(SystemExit) as exc:
        run(["-p", "123456"])
    assert exc.value.code == 2


def test_errors_go_to_stderr_with_exit_1(monkeypatch, capsys) -> None:
    s = Stub(fail=api.NoDeviceError("No device to send to."))
    monkeypatch.setattr(cli.api, "cast", s.cast)
    assert cli.main(["dQw4w9WgXcQ"]) == 1
    captured = capsys.readouterr()
    assert captured.err == "yttv: No device to send to.\n"
    assert captured.out == ""


def test_version_flag() -> None:
    with pytest.raises(SystemExit) as exc:
        run(["--version"])
    assert exc.value.code == 0


def test_verbose_keeps_httpx_quiet_unless_doubled() -> None:
    root = logging.getLogger()
    saved = root.level, list(root.handlers)
    try:
        for name in ("httpx", "httpcore"):
            logging.getLogger(name).setLevel(logging.NOTSET)
        cli.configure_logging(1)
        assert logging.getLogger("yttv").getEffectiveLevel() == logging.INFO
        assert logging.getLogger("httpx").getEffectiveLevel() == logging.WARNING

        for name in ("httpx", "httpcore"):
            logging.getLogger(name).setLevel(logging.NOTSET)
        root.setLevel(logging.WARNING)
        cli.configure_logging(2)
        assert logging.getLogger("httpx").getEffectiveLevel() == logging.DEBUG
    finally:
        root.setLevel(saved[0])
        root.handlers[:] = saved[1]
        for name in ("httpx", "httpcore"):
            logging.getLogger(name).setLevel(logging.NOTSET)


def test_format_device_marks_last_used_and_expiry() -> None:
    line = cli.format_device(LIVING, now_ms=0)
    assert line.startswith("* Living room")
    assert "token until 2096-" in line
    line = cli.format_device(BEDROOM, now_ms=FAR_FUTURE)
    assert line.startswith("  Bedroom") and line.endswith("token expired")


def test_appletv_option_pairs_then_attaches(stub: Stub, monkeypatch) -> None:
    pytest.importorskip("pyatv")
    from yttv.backends import appletv

    monkeypatch.setattr(appletv, "ensure_paired", lambda host, ask: "Wohnzimmer")
    attached: list[tuple] = []

    def attach(device, backend, address, **data):
        attached.append((device, backend, address, data))
        return BEDROOM

    monkeypatch.setattr(cli.api, "attach", attach)
    code, out, _ = run(["-d", "bed", "--appletv", "192.168.178.27", "dQw4w9WgXcQ"])
    assert code == 0
    assert attached == [("bed", "appletv", "192.168.178.27", {"apple_name": "Wohnzimmer"})]
    assert out.splitlines()[0] == "Bedroom is reached through Wohnzimmer at 192.168.178.27."
    assert stub.calls == [("cast", ["dQw4w9WgXcQ"], False, BEDROOM)]


def test_appletv_option_alone_only_attaches(stub: Stub, monkeypatch) -> None:
    pytest.importorskip("pyatv")
    from yttv.backends import appletv

    monkeypatch.setattr(appletv, "ensure_paired", lambda host, ask: "Wohnzimmer")
    monkeypatch.setattr(cli.api, "attach", lambda device, backend, address, **d: BEDROOM)
    code, out, _ = run(["--appletv", "192.168.178.27"])
    assert code == 0 and stub.calls == []


def test_appletv_option_reports_pairing_failure(stub: Stub, monkeypatch) -> None:
    pytest.importorskip("pyatv")
    from yttv.backends import appletv

    def fail(host, ask):
        raise appletv.AppleTVError("no Apple TV answered at 1.2.3.4")

    monkeypatch.setattr(appletv, "ensure_paired", fail)
    assert cli.main(["--appletv", "1.2.3.4"]) == 1


def test_search_lists_found_devices_and_can_play(stub: Stub, monkeypatch) -> None:
    from ytlounge import Screen as _Screen

    found = [Device(None, backend="dial", address="10.0.0.9", backend_data={"friendly_name": "Fire TV"})]
    seen: dict = {}

    def discover(**kw):
        seen.update(kw)
        return found

    monkeypatch.setattr(cli.api, "discover", discover)
    code, out, _ = run(["-s", "-t", "2", "-i", "10.0.0.5", "--host", "10.0.0.9"])
    assert code == 0
    assert seen == {"timeout": 2.0, "local_address": "10.0.0.5", "hosts": ["10.0.0.9"]}
    assert out.splitlines() == ["  Fire TV                  10.0.0.9 (dial)              no screen id yet"]
    assert stub.calls == []

    code, out, _ = run(["-s", "dQw4w9WgXcQ"])
    assert code == 0 and stub.calls == [("cast", ["dQw4w9WgXcQ"], False, None)]


def test_search_without_result_points_to_doctor(stub: Stub, monkeypatch) -> None:
    monkeypatch.setattr(cli.api, "discover", lambda **kw: [])
    code, out, _ = run(["--search"])
    assert code == 0 and "--doctor" in out


def test_doctor_option_runs_the_doctor(stub: Stub, monkeypatch) -> None:
    from yttv import doctor

    seen: dict = {}

    def fake_run(out, **kw):
        seen.update(kw)
        print("diagnosis", file=out)
        return 1

    monkeypatch.setattr(doctor, "run", fake_run)
    code, out, _ = run(["--doctor", "--host", "1.2.3.4", "-t", "1"])
    assert code == 1 and out == "diagnosis\n"
    assert seen == {"timeout": 1.0, "local_address": None, "hosts": ["1.2.3.4"]}
    assert stub.calls == []
