"""Device backends: each one brings the YouTube app to the front on a TV.
Playing is not their job, see :mod:`ytlounge`.

A backend registers a :class:`Launcher` under its name. ``get_launcher``
returns ``None`` for unknown names and for screens paired by TV code
without any backend, in which case the app has to be open already.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from ..cache import Device


class Launcher(Protocol):
    def launch(self, device: Device, *, timeout: float) -> None:
        """Wake the TV if needed and bring the YouTube app to the front.
        Return when the app is ready to take Lounge commands."""


_LAUNCHERS: dict[str, Launcher] = {}


def register(name: str, launcher: Launcher) -> None:
    _LAUNCHERS[name] = launcher


def get_launcher(name: str | None) -> Launcher | None:
    if name is None:
        return None
    return _LAUNCHERS.get(name)
