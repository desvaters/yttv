"""Device backends: each one brings the YouTube app to the front on a TV.
Playing is not their job, see :mod:`ytlounge`.

Backends are imported lazily by name (``yttv.backends.<name>``) so that a
missing optional dependency only matters when that backend is actually
used. Screens paired by TV code without a backend get ``None``: the app
has to be open already.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from ..cache import Device

KNOWN = ("appletv", "cast", "dial")

# Per backend: the pip extra that provides it, the import it needs, and a
# caveat worth stating in the error.
_EXTRAS = {
    "appletv": ("appletv", "pyatv", ""),
    "cast": ("cast", "pychromecast", ""),
}


class BackendUnavailable(Exception):
    """The device names a backend whose dependency is not installed."""


class Launcher(Protocol):
    def launch(self, device: Device, *, timeout: float) -> None:
        """Wake the TV if needed and bring the YouTube app to the front.
        Return when the app is ready to take Lounge commands."""


_LAUNCHERS: dict[str, Launcher] = {}


def register(name: str, launcher: Launcher) -> None:
    _LAUNCHERS[name] = launcher


def get_launcher(name: str | None) -> Launcher | None:
    """The launcher for a backend name, importing the backend on first use.

    Returns ``None`` for no backend or an unknown name. Raises
    :class:`BackendUnavailable` when the backend exists but its optional
    dependency is missing, so the caller can say what to install.
    """
    if name is None:
        return None
    if name in _LAUNCHERS:
        return _LAUNCHERS[name]
    if name not in KNOWN:
        return None
    try:
        importlib.import_module(f"{__name__}.{name}")
    except ImportError as exc:
        extra, module, caveat = _EXTRAS.get(name, (name, name, ""))
        raise BackendUnavailable(
            f"The {name} backend needs '{module}', which is not installed "
            f"(pip install 'yttv[{extra}]'{caveat}): {exc}"
        ) from exc
    return _LAUNCHERS.get(name)
