"""Persistent store of paired screens.

yttv keeps its own file. On the very first start, if ytcast's cache exists,
its paired devices are taken over once so nobody has to re-pair. After that
ytcast's file is never read again and never written: the two schemas are
free to drift apart.

The file holds lounge tokens, so it is written with mode 0600 and replaced
atomically.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from ytlounge import Screen

log = logging.getLogger(__name__)

FORMAT_VERSION = 1
FILE_NAME = "devices.json"
YTCAST_CACHE = Path("~/.cache/ytcast/ytcast.json")


class CacheError(Exception):
    """The cache file exists but cannot be used."""


def default_path() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or "~/.cache"
    return Path(base).expanduser() / "yttv" / FILE_NAME


@dataclass
class Device:
    """A TV known to yttv: the paired screen plus, if a backend found it, how
    to reach it. ``backend`` names the module (``dial``, ``cast``,
    ``appletv``); ``backend_data`` is that backend's own bag of details."""

    screen: Screen
    last_used: bool = False
    backend: str | None = None
    address: str | None = None
    backend_data: dict[str, Any] = field(default_factory=dict)

    @property
    def label(self) -> str:
        """Something a human recognises: the device's friendly name if a
        backend knows one, else the screen's name from pairing."""
        return str(self.backend_data.get("friendly_name") or self.screen.name or self.screen.screen_id)

    def to_json(self) -> dict[str, Any]:
        return {
            "screen": {
                "screen_id": self.screen.screen_id,
                "lounge_token": self.screen.lounge_token,
                "expiration": self.screen.expiration,
                "name": self.screen.name,
            },
            "last_used": self.last_used,
            "backend": self.backend,
            "address": self.address,
            "backend_data": self.backend_data,
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Device:
        try:
            s = data["screen"]
            return cls(
                screen=Screen(
                    screen_id=str(s["screen_id"]),
                    lounge_token=str(s["lounge_token"]),
                    expiration=int(s["expiration"]),
                    name=str(s.get("name", "")),
                ),
                last_used=bool(data.get("last_used", False)),
                backend=data.get("backend"),
                address=data.get("address"),
                backend_data=dict(data.get("backend_data") or {}),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise CacheError(f"malformed device entry: {exc}") from exc


class Cache:
    """The list of known devices, backed by one JSON file."""

    def __init__(self, path: Path | None = None, ytcast_path: Path | None = None):
        self.path = path or default_path()
        self.ytcast_path = (ytcast_path or YTCAST_CACHE).expanduser()
        self.devices: list[Device] = []

    # -- persistence ----------------------------------------------------------

    def load(self) -> list[Device]:
        """Read the cache. A missing file yields an empty list, unless
        ytcast's cache exists: then its devices are migrated and saved."""
        if self.path.exists():
            self.devices = _read(self.path)
        elif self.ytcast_path.exists():
            self.devices = migrate_from_ytcast(self.ytcast_path)
            if self.devices:
                log.info("took over %d device(s) from %s", len(self.devices), self.ytcast_path)
                self.save()
        else:
            self.devices = []
        return self.devices

    def save(self) -> None:
        """Write atomically with mode 0600: the file holds lounge tokens."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": FORMAT_VERSION,
            "devices": [d.to_json() for d in self.devices],
        }
        text = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
        fd, tmp_name = tempfile.mkstemp(dir=self.path.parent, prefix=".devices-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(text)
            os.chmod(tmp_name, 0o600)
            os.replace(tmp_name, self.path)
        except BaseException:
            Path(tmp_name).unlink(missing_ok=True)
            raise

    # -- queries and updates ----------------------------------------------------

    def find(self, screen_id: str) -> Device | None:
        return next((d for d in self.devices if d.screen.screen_id == screen_id), None)

    def last_used(self) -> Device | None:
        return next((d for d in self.devices if d.last_used), None)

    def upsert(self, device: Device) -> Device:
        """Insert ``device`` or replace the entry with the same screen id.
        Does not save."""
        for i, existing in enumerate(self.devices):
            if existing.screen.screen_id == device.screen.screen_id:
                self.devices[i] = device
                return device
        self.devices.append(device)
        return device

    def set_last_used(self, device: Device) -> None:
        """Mark ``device`` as the one to use by default. Does not save."""
        for d in self.devices:
            d.last_used = d.screen.screen_id == device.screen.screen_id


def _read(path: Path) -> list[Device]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise CacheError(f"cannot read {path}: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("devices"), list):
        raise CacheError(f"{path}: expected an object with a 'devices' list")
    version = data.get("version")
    if version != FORMAT_VERSION:
        raise CacheError(f"{path}: unsupported format version {version!r}")
    return [Device.from_json(entry) for entry in data["devices"]]


# -- migration from ytcast --------------------------------------------------


def migrate_from_ytcast(path: Path) -> list[Device]:
    """Convert ytcast's cache into devices.

    ytcast's file is a JSON *list* of ``{Device, Remote, LastUsed}``.
    ``Device`` is ``null`` for screens paired by TV code and a DIAL
    description otherwise. Entries that cannot be understood are skipped
    with a warning: this is a convenience, not a contract.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        log.warning("ignoring ytcast cache %s: %s", path, exc)
        return []
    if not isinstance(data, list):
        log.warning("ignoring ytcast cache %s: not a list", path)
        return []

    devices: list[Device] = []
    for entry in data:
        device = _ytcast_entry(entry)
        if device is None:
            log.warning("skipping unreadable entry in %s", path)
        else:
            devices.append(device)
    return devices


def _ytcast_entry(entry: Any) -> Device | None:
    if not isinstance(entry, dict):
        return None
    remote = entry.get("Remote")
    if not isinstance(remote, dict):
        return None
    try:
        screen = Screen(
            screen_id=str(remote["ScreenId"]),
            lounge_token=str(remote["LoungeToken"]),
            expiration=int(remote["Expiration"]),
            name=str(remote.get("ScreenName") or ""),
        )
    except (KeyError, TypeError, ValueError):
        return None
    if not screen.screen_id:
        return None

    device = Device(screen=screen, last_used=bool(entry.get("LastUsed", False)))
    dial = entry.get("Device")
    if isinstance(dial, dict):
        device.backend = "dial"
        device.address = urlsplit(str(dial.get("Location") or "")).hostname
        wakeup = dial.get("Wakeup") if isinstance(dial.get("Wakeup"), dict) else {}
        device.backend_data = {
            "unique_service_name": dial.get("UniqueServiceName"),
            "location": dial.get("Location"),
            "application_url": dial.get("ApplicationUrl"),
            "friendly_name": dial.get("FriendlyName"),
            # Go serialises time.Duration as nanoseconds.
            "wake_mac": wakeup.get("Mac") or None,
            "wake_timeout_s": (int(wakeup["Timeout"]) / 1e9) if wakeup.get("Timeout") else None,
        }
    return device
