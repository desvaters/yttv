"""Apple TV backend via the Companion protocol, built on pyatv.

Requires the ``appletv`` extra and Python < 3.14. Companion is the only
pyatv protocol that can launch an app; it needs a one-time PIN pairing
whose credentials pyatv keeps in ``~/.pyatv.conf``.

A Lounge command alone does not do the job on tvOS: it brings the app to
its start page but plays nothing. Launching through Companion first, then
sending the Lounge command, does.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Callable, Coroutine
from typing import Any

import pyatv
from pyatv.const import PowerState, Protocol
from pyatv.interface import BaseConfig, Storage
from pyatv.storage.file_storage import FileStorage

from . import register
from ..cache import Device

log = logging.getLogger(__name__)

BUNDLE_ID = "com.google.ios.youtube"  # the same on tvOS as on iOS

# Seconds the YouTube app gets after launch before the Lounge command goes
# out. Overridable per device with backend_data["settle_seconds"].
DEFAULT_SETTLE_SECONDS = 3.0
SCAN_TIMEOUT = 5


class AppleTVError(Exception):
    """Reaching, pairing or driving the Apple TV failed."""


def _run(coro: Coroutine[Any, Any, Any], timeout: float) -> Any:
    """Run ``coro`` on its own event loop in a helper thread.

    Always a fresh thread: the caller may itself be inside a running loop
    (a web app, for instance), where asyncio.run() would blow up.
    """
    result: list[Any] = []
    failure: list[BaseException] = []

    def body() -> None:
        try:
            result.append(asyncio.run(coro))
        except BaseException as exc:  # carried back to the caller
            failure.append(exc)

    thread = threading.Thread(target=body, name="yttv-appletv", daemon=True)
    thread.start()
    thread.join(timeout)
    if thread.is_alive():
        raise AppleTVError(f"gave up after {timeout:.0f} s")
    if failure:
        raise failure[0]
    return result[0]


async def _storage() -> Storage:
    storage = FileStorage.default_storage(asyncio.get_running_loop())
    await storage.load()
    return storage


async def _find(host: str, storage: Storage) -> BaseConfig:
    """The device at ``host``, by unicast scan. mDNS discovery is not
    used: unicast replies to it are dropped by a stateful firewall on
    many machines, a direct probe is not."""
    loop = asyncio.get_running_loop()
    configs = await pyatv.scan(loop, hosts=[host], timeout=SCAN_TIMEOUT, storage=storage)
    if not configs:
        raise AppleTVError(f"no Apple TV answered at {host}")
    return configs[0]


def _companion_paired(config: BaseConfig) -> bool:
    service = config.get_service(Protocol.Companion)
    return service is not None and bool(service.credentials)


async def _launch(host: str, settle: float) -> None:
    storage = await _storage()
    config = await _find(host, storage)
    if not _companion_paired(config):
        raise AppleTVError(
            f"{config.name} at {host} is not paired for Companion yet; "
            "run `yttv --appletv HOST` once to pair with the PIN it shows"
        )
    atv = await pyatv.connect(config, asyncio.get_running_loop(), protocol=Protocol.Companion, storage=storage)
    try:
        if atv.power.power_state == PowerState.Off:
            log.info("%s is off, turning it on", config.name)
            await atv.power.turn_on()
        log.info("launching %s on %s", BUNDLE_ID, config.name)
        await atv.apps.launch_app(BUNDLE_ID)
        await asyncio.sleep(settle)
    finally:
        atv.close()


class AppleTVLauncher:
    def launch(self, device: Device, *, timeout: float) -> None:
        if not device.address:
            raise AppleTVError(f"{device.label} has no address; attach it with `yttv --appletv HOST`")
        settle = float(device.backend_data.get("settle_seconds", DEFAULT_SETTLE_SECONDS))
        _run(_launch(device.address, settle), timeout)


register("appletv", AppleTVLauncher())


# -- pairing ------------------------------------------------------------------


async def _pair(host: str, ask_pin: Callable[[str], str]) -> str:
    storage = await _storage()
    config = await _find(host, storage)
    if _companion_paired(config):
        return config.name
    if config.get_service(Protocol.Companion) is None:
        raise AppleTVError(f"{config.name} at {host} does not offer Companion")
    pairing = await pyatv.pair(config, Protocol.Companion, asyncio.get_running_loop(), storage=storage)
    try:
        await pairing.begin()
        pin = ask_pin(config.name).strip()
        if not pin.isdigit():
            raise AppleTVError("the PIN is digits only")
        pairing.pin(int(pin))
        await pairing.finish()
        if not pairing.has_paired:
            raise AppleTVError("pairing was not accepted")
        await storage.save()
    finally:
        await pairing.close()
    return config.name


def ensure_paired(host: str, ask_pin: Callable[[str], str], timeout: float = 120.0) -> str:
    """Pair Companion with the Apple TV at ``host`` unless already done.
    ``ask_pin`` gets the device name and returns the PIN shown on screen.
    Returns the device name."""
    return _run(_pair(host, ask_pin), timeout)
