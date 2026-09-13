"""Tests against the real Lounge API. They need a paired screen and network
and are selected with ``pixi run test-device``.

The screen comes from ytcast's cache for now; yttv's own cache does not
exist yet. Only ``connect`` and ``refresh`` run here: neither changes what
the TV shows.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from ytlounge import Lounge, Screen

pytestmark = pytest.mark.device

YTCAST_CACHE = Path(os.environ.get("YTCAST_CACHE", "~/.cache/ytcast/ytcast.json")).expanduser()


@pytest.fixture
def screen() -> Screen:
    if not YTCAST_CACHE.exists():
        pytest.skip(f"no ytcast cache at {YTCAST_CACHE}")
    entries = json.loads(YTCAST_CACHE.read_text())
    remote = next(e["Remote"] for e in entries if e.get("LastUsed"))
    return Screen(
        screen_id=remote["ScreenId"],
        lounge_token=remote["LoungeToken"],
        expiration=int(remote["Expiration"]),
        name=remote["ScreenName"],
    )


def test_connect_yields_session_ids(screen: Screen) -> None:
    with Lounge(name="yttv-test") as lounge:
        session = lounge.connect(screen)
    assert session.sid and session.gsessionid


def test_refresh_returns_usable_token(screen: Screen) -> None:
    with Lounge(name="yttv-test") as lounge:
        refreshed = lounge.refresh(screen)
        assert refreshed.lounge_token
        assert not refreshed.is_expired()
        assert refreshed.expiration > screen.expiration - 1000
        # The new token must open a session, and the old one must still work:
        # a refresh is not a revocation.
        assert lounge.connect(refreshed).sid
        assert lounge.connect(screen).sid
