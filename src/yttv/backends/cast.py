"""Google Cast backend (Samsung Tizen and other Cast TVs), built on
pychromecast. Requires the ``cast`` extra.

Only the launch and the screen id come from the Cast channel: the YouTube
receiver answers ``getMdxSessionStatus`` on its own namespace with the
``screenId`` that the Lounge API needs. Play and queue never go through
Cast. That also keeps pychromecast's YouTube session code (inherited from
the unmaintained casttube, and broken against today's Lounge endpoint) out
of the picture: this module talks to the receiver with its own tiny
controller.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Any

import pychromecast
from pychromecast.config import APP_YOUTUBE
from pychromecast.controllers import BaseController
from pychromecast.discovery import CastBrowser, SimpleCastListener

from ytlounge import Screen

from . import register
from ..cache import Device

log = logging.getLogger(__name__)

NAMESPACE = "urn:x-cast:com.google.youtube.mdx"
TYPE_GET_STATUS = "getMdxSessionStatus"
TYPE_STATUS = "mdxSessionStatus"

CONNECT_TIMEOUT = 10.0
DISCOVERY_TIMEOUT = 5.0
STATUS_TIMEOUT = 15.0


class CastError(Exception):
    """The Cast device could not be reached or gave no screen id."""


@dataclass(frozen=True)
class Info:
    """What a probe learns about a Cast device."""

    uuid: str
    friendly_name: str
    model: str
    host: str


class MdxController(BaseController):
    """Asks the YouTube receiver for its screen id. Sending on this
    namespace launches the YouTube app if it is not running."""

    def __init__(self) -> None:
        super().__init__(NAMESPACE, APP_YOUTUBE)
        self.screen_id: str | None = None
        self._got_status = threading.Event()

    def request_screen_id(self, timeout: float) -> str | None:
        self._got_status.clear()
        self.send_message({"type": TYPE_GET_STATUS})
        if not self._got_status.wait(timeout):
            return None
        return self.screen_id

    def receive_message(self, _message: Any, data: dict) -> bool:
        if data.get("type") == TYPE_STATUS:
            self.screen_id = (data.get("data") or {}).get("screenId") or None
            self._got_status.set()
            return True
        return False


def _connect(host: str, timeout: float) -> tuple[Any, Any]:
    """The Chromecast object for ``host`` plus the browser to stop later.

    The host is polled directly (``known_hosts``), so no mDNS reply has to
    make it through a firewall that drops unicast answers (see the doctor).
    """
    found = threading.Event()
    browser: Any = None

    def added(uuid: Any, _service: str) -> None:
        info = browser.devices.get(uuid)
        if info is not None and info.host == host:
            found.set()

    browser = CastBrowser(SimpleCastListener(add_callback=added), known_hosts=[host])
    browser.start_discovery()
    if not found.wait(timeout):
        browser.stop_discovery()
        raise CastError(f"no Cast device answered at {host}")
    info = next(i for i in browser.devices.values() if i.host == host)
    try:
        cast = pychromecast.get_chromecast_from_cast_info(info, browser.zc, timeout=timeout)
        cast.wait(timeout=timeout)
    except Exception:
        browser.stop_discovery()
        raise
    return cast, browser


def _info(cast: Any) -> Info:
    ci = cast.cast_info
    model = str(ci.model_name or "")
    if model.lower().startswith("unknown"):
        model = ""  # pychromecast's placeholder until mDNS fills it in
    return Info(uuid=str(ci.uuid), friendly_name=str(ci.friendly_name or ""), model=model, host=str(ci.host))


def probe(host: str, timeout: float = CONNECT_TIMEOUT) -> Info:
    """Connect once and report what is there. Does not launch anything."""
    cast, browser = _connect(host, timeout)
    try:
        return _info(cast)
    finally:
        cast.disconnect()
        browser.stop_discovery()


def screen_id(host: str, *, timeout: float) -> tuple[str, Info]:
    """Bring up the YouTube app on the Cast device at ``host`` and return
    its screen id together with the device's details."""
    cast, browser = _connect(host, min(timeout, CONNECT_TIMEOUT))
    try:
        info = _info(cast)
        controller = MdxController()
        cast.register_handler(controller)
        log.info("asking %s for its YouTube screen id", info.friendly_name or host)
        found = controller.request_screen_id(min(timeout, STATUS_TIMEOUT))
        if not found:
            raise CastError(
                f"{info.friendly_name or host} gave no screen id. Is the YouTube app installed, "
                "or is the TV showing a dialog (Samsung asks to accept Cast terms on first use)?"
            )
        return found, info
    finally:
        cast.disconnect()
        browser.stop_discovery()


class CastLauncher:
    def launch(self, device: Device, *, timeout: float) -> None:
        if not device.address:
            raise CastError(f"{device.label} has no address; attach it with `yttv --cast HOST`")
        found, info = screen_id(device.address, timeout=timeout)
        device.backend_data.setdefault("cast_uuid", info.uuid)
        if info.friendly_name:
            device.backend_data["friendly_name"] = info.friendly_name
        if device.screen is None or device.screen.screen_id != found:
            log.info("%s reports screen id %s…", device.label, found[:8])
            device.screen = Screen(found, "", 0, name=device.label)


register("cast", CastLauncher())
