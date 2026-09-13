"""ytlounge: a client for the YouTube Lounge API.

The Lounge API is the unofficial protocol that a phone or browser uses to
drive the YouTube app on a TV: pair with a screen, play a video, append to the
queue. Every way of reaching a TV (Cast, Apple TV, DIAL) ends up here.

Scope of this package: a ``screen_id`` goes in, play and queue come out.
It knows nothing about device discovery, app launching, caches or file
paths. That boundary is deliberate and enforced by tests: ytlounge must stay
extractable into its own distribution without any consumer changing an
import.
"""

from .client import Lounge, Session
from .models import (
    CommandError,
    LoungeError,
    PairingError,
    Screen,
    SessionError,
    TokenError,
)

__all__ = [
    "CommandError",
    "Lounge",
    "LoungeError",
    "PairingError",
    "Screen",
    "Session",
    "SessionError",
    "TokenError",
]
