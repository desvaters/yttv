"""DIAL backend for Fire TV, WebOS and similar devices.

DIAL is the protocol behind the "Play on TV" button of the YouTube app on
devices without Cast: SSDP finds the TV, a small REST interface on it
describes, launches and reports the state of apps. The running YouTube app
reports its ``screenId`` there; the rest is the Lounge API.

There is no maintained Python DIAL library, so this is self-built. Only
httpx and the standard library are needed, Wake-on-LAN included.
"""

from __future__ import annotations

import logging
import socket
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from urllib.parse import urljoin
from xml.etree import ElementTree

import httpx

from ytlounge import Screen

from . import register
from .. import ssdp
from ..cache import Device

log = logging.getLogger(__name__)

APP_NAME = "YouTube"
REQUEST_TIMEOUT = 5.0
POLL_INTERVAL = 1.0
# Seconds the app gets after reporting "running" before the Lounge command.
DEFAULT_SETTLE_SECONDS = 2.0


class DialError(Exception):
    """The device could not be reached, described or driven."""


@dataclass(frozen=True)
class Description:
    friendly_name: str
    application_url: str


@dataclass(frozen=True)
class AppInfo:
    state: str  # running, stopped, hidden, installable=<url>
    screen_id: str | None
    raw: str = ""

    @property
    def running(self) -> bool:
        return self.state == "running"


def _client(local_address: str | None = None) -> httpx.Client:
    transport = httpx.HTTPTransport(local_address=local_address) if local_address else None
    return httpx.Client(timeout=REQUEST_TIMEOUT, transport=transport)


def app_url(application_url: str, app: str = APP_NAME) -> str:
    """The app resource: Application-URL plus the app name. The header may
    or may not end in a slash."""
    return urljoin(application_url.rstrip("/") + "/", app)


# -- REST -------------------------------------------------------------------


def describe(client: httpx.Client, location: str) -> Description:
    """Fetch the device description. Application-URL comes as a header,
    the friendly name from the UPnP XML (namespace-agnostic)."""
    try:
        response = client.get(location)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise DialError(f"description {location}: {exc}") from exc
    application_url = response.headers.get("Application-URL", "").strip()
    if not application_url:
        raise DialError(f"{location} is not a DIAL device (no Application-URL header)")
    try:
        root = ElementTree.fromstring(response.text)
        name = root.findtext(".//{*}friendlyName") or ""
    except ElementTree.ParseError as exc:
        raise DialError(f"description {location}: bad XML: {exc}") from exc
    return Description(friendly_name=name.strip(), application_url=application_url)


def app_info(client: httpx.Client, application_url: str) -> AppInfo:
    """State of the YouTube app and, when it is running, its screen id."""
    url = app_url(application_url)
    try:
        response = client.get(url)
    except httpx.HTTPError as exc:
        raise DialError(f"app info {url}: {exc}") from exc
    if response.status_code == 404:
        raise DialError(f"{APP_NAME} is not installed on this device ({url} is 404)")
    if response.status_code != 200:
        raise DialError(f"app info {url}: HTTP {response.status_code}")
    try:
        root = ElementTree.fromstring(response.text)
    except ElementTree.ParseError as exc:
        raise DialError(f"app info {url}: bad XML: {exc}") from exc
    state = (root.findtext("{*}state") or "").strip()
    screen_id = root.findtext(".//{*}additionalData/{*}screenId")
    return AppInfo(state=state, screen_id=(screen_id or "").strip() or None, raw=response.text)


def launch_app(client: httpx.Client, application_url: str) -> None:
    """Start the YouTube app. 201 means launched, 200 already running."""
    url = app_url(application_url)
    try:
        response = client.post(url, content=b"", headers={"Content-Type": "text/plain; charset=utf-8"})
    except httpx.HTTPError as exc:
        raise DialError(f"launch {url}: {exc}") from exc
    if response.status_code not in (200, 201):
        raise DialError(f"launch {url}: HTTP {response.status_code}")


# -- Wake-on-LAN ---------------------------------------------------------------


def magic_packet(mac: str) -> bytes:
    digits = mac.replace(":", "").replace("-", "").replace(".", "")
    if len(digits) != 12:
        raise ValueError(f"not a MAC address: {mac!r}")
    return b"\xff" * 6 + bytes.fromhex(digits) * 16


def wake(mac: str, *, broadcast: str = "255.255.255.255", port: int = 9) -> None:
    packet = magic_packet(mac)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.sendto(packet, (broadcast, port))


# -- discovery ----------------------------------------------------------------


def _device_from(client: httpx.Client, response: ssdp.Response) -> Device | None:
    try:
        description = describe(client, response.location)
    except DialError as exc:
        log.warning("skipping %s: %s", response.address, exc)
        return None
    wakeup = response.wakeup
    timeout_s = None
    if wakeup.get("timeout", "").isdigit():
        timeout_s = float(wakeup["timeout"])
    return Device(
        screen=None,
        backend="dial",
        address=response.address,
        backend_data={
            "unique_service_name": response.usn,
            "location": response.location,
            "application_url": description.application_url,
            "friendly_name": description.friendly_name,
            "wake_mac": wakeup.get("mac") or None,
            "wake_timeout_s": timeout_s,
        },
    )


def discover(
    *,
    timeout: float = 4.0,
    local_address: str | None = None,
    unicast_hosts: list[str] | None = None,
) -> list[Device]:
    """Find DIAL devices: one M-SEARCH, then every reply is described in
    parallel. Devices that fail to describe are skipped with a warning."""
    responses = ssdp.search(
        ssdp.ST_DIAL, timeout=timeout, local_address=local_address, unicast_hosts=unicast_hosts
    )
    if not responses:
        return []
    with _client(local_address) as client, ThreadPoolExecutor(max_workers=8) as pool:
        devices = list(pool.map(lambda r: _device_from(client, r), responses))
    return [d for d in devices if d is not None]


# -- launcher ---------------------------------------------------------------


class DialLauncher:
    def __init__(self, sleep=time.sleep, clock=time.monotonic, wake=wake):
        self._sleep = sleep
        self._clock = clock
        self._wake = wake

    def launch(self, device: Device, *, timeout: float) -> None:
        application_url = device.backend_data.get("application_url")
        if not application_url:
            raise DialError(f"{device.label} has no application URL; run a search (-s) first")
        deadline = self._clock() + timeout
        settle = float(device.backend_data.get("settle_seconds", DEFAULT_SETTLE_SECONDS))
        with _client(device.backend_data.get("local_address")) as client:
            info = self._reach(client, device, application_url, deadline)
            if not info.running:
                log.info("starting %s on %s", APP_NAME, device.label)
                launch_app(client, application_url)
                info = self._await_running(client, application_url, deadline, device.label)
                self._sleep(settle)
        if info.screen_id is None:
            raise DialError(f"{device.label} runs {APP_NAME} but reports no screen id")
        if device.screen is None or device.screen.screen_id != info.screen_id:
            log.info("%s reports screen id %s…", device.label, info.screen_id[:8])
            device.screen = Screen(info.screen_id, "", 0, name=device.label)

    def _reach(self, client: httpx.Client, device: Device, application_url: str, deadline: float) -> AppInfo:
        """App info, waking the device first when it does not answer."""
        try:
            return app_info(client, application_url)
        except DialError as exc:
            mac = device.backend_data.get("wake_mac")
            if not mac or "is not installed" in str(exc):
                raise
            log.info("%s does not answer (%s), sending Wake-on-LAN to %s", device.label, exc, mac)
        self._wake(mac)
        while True:
            self._sleep(POLL_INTERVAL)
            try:
                return app_info(client, application_url)
            except DialError as exc:
                if self._clock() >= deadline:
                    raise DialError(f"{device.label} did not wake up in time: {exc}") from exc

    def _await_running(self, client: httpx.Client, application_url: str, deadline: float, label: str) -> AppInfo:
        while True:
            self._sleep(POLL_INTERVAL)
            info = app_info(client, application_url)
            if info.running and info.screen_id:
                return info
            if self._clock() >= deadline:
                raise DialError(f"{APP_NAME} on {label} did not come up in time (state {info.state!r})")


register("dial", DialLauncher())
