"""yttv: play YouTube videos on a TV.

Device backends (Cast, Apple TV, DIAL) bring the YouTube app to the front and
obtain the screen's ``screen_id``; :mod:`ytlounge` does the actual playing.

The public API is three functions::

    import yttv
    yttv.devices()                      # paired screens, from the cache
    yttv.cast(["dQw4w9WgXcQ"])          # play now on the last used screen
    yttv.cast([url1, url2], queue=True) # append to the queue
    yttv.pair("123 456 789")            # link a screen by TV code
"""

from .api import (
    CastError,
    InvalidVideoError,
    NoDeviceError,
    PairError,
    YttvError,
    cast,
    devices,
    pair,
)
from .cache import Cache, Device

__all__ = [
    "Cache",
    "CastError",
    "Device",
    "InvalidVideoError",
    "NoDeviceError",
    "PairError",
    "YttvError",
    "cast",
    "devices",
    "pair",
]
