"""The library API: what a program uses instead of spawning a binary.

Three calls cover the whole job:

- :func:`devices` lists the paired screens from the cache. No network.
- :func:`cast` sends videos to a screen: play now, or append to the queue.
- :func:`pair` links a new screen with the code the TV shows.

Every failure is a :class:`YttvError` subclass with a message meant for
a human, so a caller can show it as is.
"""

from __future__ import annotations

import getpass
import logging
import socket
from collections.abc import Sequence

from ytlounge import Lounge, LoungeError, PairingError, Screen, SessionError, TokenError
from ytlounge.video import Video, parse_video

from .backends import KNOWN, BackendUnavailable, get_launcher
from .cache import Cache, CacheError, Device

log = logging.getLogger(__name__)

# Budget for waking a sleeping TV and bringing the app to the front. Wake-on-LAN
# plus a cold app start on a TV takes well over a minute on some devices.
DEFAULT_TIMEOUT = 90.0

# Refresh a token this long before it expires rather than at the last moment.
TOKEN_MARGIN_MS = 24 * 3600 * 1000


class YttvError(Exception):
    """Base class. The message is fit for showing to a user."""


class NoDeviceError(YttvError):
    """No paired screen to send to."""


class InvalidVideoError(YttvError):
    """An argument was neither a video id nor a YouTube URL."""


class CastError(YttvError):
    """The screen could not be reached or rejected the command."""


class PairError(YttvError):
    """Pairing with the TV code failed."""


def remote_name() -> str:
    """How this machine shows up in the TV's list of connected devices."""
    try:
        return f"{getpass.getuser()}@{socket.gethostname()}"
    except OSError:
        return "yttv"


def _open_cache(cache: Cache | None) -> Cache:
    cache = cache or Cache()
    try:
        cache.load()
    except CacheError as exc:
        raise YttvError(str(exc)) from exc
    return cache


def devices(cache: Cache | None = None) -> list[Device]:
    """The paired screens. Reads the cache only, never the network."""
    return list(_open_cache(cache).devices)


def parse_videos(items: Sequence[str]) -> list[Video]:
    """Video ids or URLs to :class:`Video` objects, all or nothing."""
    if not items:
        raise InvalidVideoError("No video given.")
    videos: list[Video] = []
    bad: list[str] = []
    for item in items:
        try:
            videos.append(parse_video(item))
        except ValueError:
            bad.append(item)
    if bad:
        raise InvalidVideoError(f"Not a video id or YouTube URL: {', '.join(bad[:3])}")
    return videos


def _select(cache: Cache, device: Device | str | None) -> Device:
    if isinstance(device, Device):
        return device
    if device is None:
        found = cache.last_used()
        if found is None and len(cache.devices) == 1:
            found = cache.devices[0]
        if found is None:
            raise NoDeviceError(
                "No device to send to. Pair one with the code from the TV app "
                "(Settings > Link with TV code)."
            )
        return found
    needle = device.lower()
    matches = [
        d
        for d in cache.devices
        if needle in d.label.lower()
        or needle in (d.address or "").lower()
        or (d.screen is not None and d.screen.screen_id.lower().startswith(needle))
    ]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise NoDeviceError(f"No device matches {device!r}.")
    names = ", ".join(m.label for m in matches)
    raise NoDeviceError(f"{device!r} is ambiguous: {names}")


def _fresh_screen(lounge: Lounge, cache: Cache, device: Device) -> Screen:
    """Refresh the lounge token when it is about to expire."""
    if device.screen is None:
        raise CastError(
            f"{device.label} has not told us its screen id yet; is the YouTube app installed on it?"
        )
    if not device.screen.is_expired(margin_ms=TOKEN_MARGIN_MS):
        return device.screen
    log.info("lounge token for %s is expiring, refreshing", device.label)
    try:
        device.screen = lounge.refresh(device.screen)
    except TokenError as exc:
        raise CastError(f"Could not renew the link to {device.label}: {exc}") from exc
    cache.save()
    return device.screen


def cast(
    videos: Sequence[str],
    *,
    queue: bool = False,
    device: Device | str | None = None,
    cache: Cache | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    lounge: Lounge | None = None,
) -> Device:
    """Send ``videos`` (ids or URLs) to a screen and return the device used.

    All videos go in one call, never one after another: the TV's queue
    gets scrambled by rapid successive sends. ``queue=True`` appends
    without interrupting playback, otherwise the first video starts now.
    ``device`` may be a :class:`Device`, a substring of a name or address,
    or ``None`` for the last used one. ``timeout`` bounds waking the TV and
    launching the app, not the Lounge requests themselves.
    """
    parsed = parse_videos(videos)
    cache = _open_cache(cache)
    target = _select(cache, device)
    own_lounge = lounge is None
    lounge = lounge or Lounge(name=remote_name())
    try:
        try:
            launcher = get_launcher(target.backend)
        except BackendUnavailable as exc:
            raise CastError(str(exc)) from exc
        if launcher is not None:
            # First, because a launcher may learn or correct the screen id
            # (DIAL reads it from the running app).
            before = target.screen
            try:
                launcher.launch(target, timeout=timeout)
            except Exception as exc:  # backends raise their own kinds
                raise CastError(f"Could not start YouTube on {target.label}: {exc}") from exc
            if target.screen is not before:
                cache.save()
        screen = _fresh_screen(lounge, cache, target)
        _send(lounge, screen, parsed, queue)
    except SessionError as exc:
        # Maybe the token died early. One refresh, one retry.
        log.info("session with %s failed (%s), refreshing token once", target.label, exc)
        try:
            assert target.screen is not None
            target.screen = lounge.refresh(target.screen)
            cache.save()
            _send(lounge, target.screen, parsed, queue)
        except LoungeError as retry_exc:
            raise CastError(f"{target.label} did not accept the request: {retry_exc}") from retry_exc
    except LoungeError as exc:
        raise CastError(f"{target.label} did not accept the request: {exc}") from exc
    finally:
        if own_lounge:
            lounge.close()
    cache.set_last_used(target)
    cache.save()
    return target


def _send(lounge: Lounge, screen: Screen, videos: list[Video], queue: bool) -> None:
    ids = [v.id for v in videos]
    if queue:
        lounge.add(screen, ids)
    else:
        lounge.play(screen, ids, start_seconds=videos[0].start_seconds)


def pair(
    code: str,
    *,
    cache: Cache | None = None,
    lounge: Lounge | None = None,
) -> Device:
    """Link a screen using the code from the TV app and make it the default.

    Returns the new device. The code is digits, sometimes shown in groups;
    spaces and dashes are ignored.
    """
    digits = "".join(ch for ch in code if ch.isdigit())
    if not 6 <= len(digits) <= 20:
        raise PairError("A TV code is 6 to 20 digits.")
    cache = _open_cache(cache)
    own_lounge = lounge is None
    lounge = lounge or Lounge(name=remote_name())
    try:
        screen = lounge.pair(digits)
    except PairingError as exc:
        raise PairError(f"The TV did not accept the code: {exc}") from exc
    finally:
        if own_lounge:
            lounge.close()
    existing = cache.find(screen.screen_id)
    if existing is not None:
        existing.screen = screen
        device = existing
    else:
        device = cache.upsert(Device(screen=screen))
    cache.set_last_used(device)
    cache.save()
    return device


def attach(
    device: Device | str | None,
    backend: str,
    address: str,
    *,
    cache: Cache | None = None,
    **backend_data: object,
) -> Device:
    """Record how to reach a screen: which backend and at what address.

    ``device`` selects like in :func:`cast`. Backend-specific pairing (a
    Companion PIN, say) is the backend's business and must happen before.
    """
    if backend not in KNOWN:
        raise YttvError(f"Unknown backend {backend!r}; one of {', '.join(KNOWN)}.")
    cache = _open_cache(cache)
    target = _select(cache, device)
    target.backend = backend
    target.address = address
    target.backend_data.update(backend_data)
    cache.save()
    return target


def add_device(
    backend: str,
    address: str,
    *,
    cache: Cache | None = None,
    **backend_data: object,
) -> Device:
    """Remember a device that needs no pairing code: its backend delivers the
    screen id at cast time (Cast, DIAL). Re-adding the same device (by
    identity) updates it and keeps its screen."""
    if backend not in KNOWN:
        raise YttvError(f"Unknown backend {backend!r}; one of {', '.join(KNOWN)}.")
    cache = _open_cache(cache)
    fresh = Device(screen=None, backend=backend, address=address, backend_data=dict(backend_data))
    known = cache.find_service(fresh.identity) if fresh.identity else None
    if known is not None:
        known.backend = backend
        known.address = address
        known.backend_data.update(backend_data)
        device = known
    else:
        device = cache.upsert(fresh)
    cache.save()
    return device


def discover(
    *,
    timeout: float = 4.0,
    local_address: str | None = None,
    hosts: Sequence[str] = (),
    cache: Cache | None = None,
) -> list[Device]:
    """Search the network for DIAL devices and remember them.

    Devices seen before keep their screen and token; only address and
    description are refreshed. ``hosts`` are probed directly in addition
    to the multicast search. Returns the devices found in this search.
    """
    from .backends import dial

    cache = _open_cache(cache)
    found = dial.discover(timeout=timeout, local_address=local_address, unicast_hosts=list(hosts))
    result: list[Device] = []
    for device in found:
        usn = device.backend_data.get("unique_service_name", "")
        known = cache.find_service(usn) if usn else None
        if known is not None:
            known.address = device.address
            known.backend = "dial"
            known.backend_data.update(device.backend_data)
            result.append(known)
        else:
            result.append(cache.upsert(device))
    if found:
        cache.save()
    return result
