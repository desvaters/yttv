"""Data types and exceptions of the Lounge client."""

from __future__ import annotations

import time
from dataclasses import dataclass, replace


class LoungeError(Exception):
    """Base class for every error raised by ytlounge."""


class PairingError(LoungeError):
    """The TV code was rejected or the pairing response was unusable."""


class TokenError(LoungeError):
    """A lounge token could not be obtained for a screen."""


class SessionError(LoungeError):
    """Opening a session (bind) with the screen failed."""


class CommandError(LoungeError):
    """The screen rejected a command such as play or add."""


@dataclass(frozen=True)
class Screen:
    """A paired TV screen.

    ``screen_id`` is the only durable key: it survives a cold start of the TV
    and is what a new ``lounge_token`` is fetched from. The token itself lives
    for roughly two weeks; ``expiration`` is a Unix timestamp in milliseconds.
    """

    screen_id: str
    lounge_token: str
    expiration: int
    name: str = ""

    def is_expired(self, now_ms: int | None = None, margin_ms: int = 0) -> bool:
        """True when the token has expired or will within ``margin_ms``."""
        now = int(time.time() * 1000) if now_ms is None else now_ms
        return self.expiration - margin_ms <= now

    def with_token(self, lounge_token: str, expiration: int) -> Screen:
        return replace(self, lounge_token=lounge_token, expiration=expiration)
