"""The Lounge client: pairing, token refresh, sessions, play and queue.

Everything here was verified request by request against a working
implementation on the wire (see NOTES.md §12 in the yttv repository).
"""

from __future__ import annotations

import random
import time
from collections.abc import Callable, Sequence
from typing import Any

import httpx

from .chunked import parse_frames, session_ids
from .models import (
    CommandError,
    PairingError,
    Screen,
    SessionError,
    TokenError,
)

BASE_URL = "https://www.youtube.com/api/lounge"
BIND_URL = f"{BASE_URL}/bc/bind"
GET_SCREEN_URL = f"{BASE_URL}/pairing/get_screen"
GET_LOUNGE_TOKEN_BATCH_URL = f"{BASE_URL}/pairing/get_lounge_token_batch"

# The screen identifies remotes by these. They mirror what a browser tab of
# youtube.com sends; a bare UA is not rejected today, but there is no reason
# to look different from the reference traffic.
DEFAULT_HEADERS = {
    "Origin": "https://www.youtube.com",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/96.0.4664.45 Safari/537.36"
    ),
}

# Wire protocol constants. VER/CVER are the channel protocol versions the
# screen expects; `app` and `device` tell it what kind of remote we are.
_CHANNEL_VERSION = "8"
_CLIENT_VERSION = "1"
_APP = "youtube-desktop"
_DEVICE = "REMOTE_CONTROL"

# Delay before every addVideo. Not cosmetic: without it consecutive adds race
# on the screen and the queue ends up incomplete or reordered.
ADD_DELAY_RANGE = (2.0, 5.0)


def _int_expiration(value: Any) -> int:
    """The API returns expiration as a string on one endpoint and as a number
    on another. Normalise to int milliseconds."""
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise TokenError(f"unusable expiration {value!r}") from exc


def _check_ids(video_ids: Sequence[str], what: str) -> list[str]:
    ids = [v for v in video_ids]
    if not ids or any(not v for v in ids):
        raise ValueError(f"{what} needs at least one non-empty video id")
    return ids


def _check_play_args(video_ids: Sequence[str], start_index: int) -> list[str]:
    ids = _check_ids(video_ids, "play")
    if not 0 <= start_index < len(ids):
        raise ValueError(f"start_index {start_index} out of range for {len(ids)} videos")
    return ids


class Session:
    """An open channel to one screen. Cheap to create, so callers open a fresh
    one per play/add call instead of keeping it around."""

    def __init__(self, lounge: Lounge, screen: Screen, sid: str, gsessionid: str):
        self._lounge = lounge
        self.screen = screen
        self.sid = sid
        self.gsessionid = gsessionid
        self._rid = 1  # the bind used RID=1; commands continue from 2

    def _next_rid(self) -> str:
        self._rid += 1
        return str(self._rid)

    def _command(self, name: str, fields: dict[str, str]) -> None:
        params = {
            "CVER": _CLIENT_VERSION,
            "RID": self._next_rid(),
            "SID": self.sid,
            "VER": _CHANNEL_VERSION,
            "gsessionid": self.gsessionid,
            "loungeIdToken": self.screen.lounge_token,
        }
        data = {"count": "1", "req0__sc": name}
        data.update({f"req0_{key}": value for key, value in fields.items()})
        try:
            response = self._lounge._client.post(BIND_URL, params=params, data=data)
        except httpx.HTTPError as exc:
            raise CommandError(f"{name}: {exc}") from exc
        if response.status_code != 200:
            raise CommandError(
                f"{name}: HTTP {response.status_code}: {response.text[:200]}"
            )

    def play(
        self,
        video_ids: Sequence[str],
        *,
        start_index: int = 0,
        start_seconds: int = 0,
    ) -> None:
        """Replace the screen's queue with ``video_ids`` and start playing."""
        ids = _check_play_args(video_ids, start_index)
        self._command(
            "setPlaylist",
            {
                "currentIndex": str(start_index),
                "currentTime": str(start_seconds),
                "videoId": ids[start_index],
                "videoIds": ",".join(ids),
            },
        )

    def add(self, video_ids: Sequence[str]) -> None:
        """Append ``video_ids`` to the screen's queue without interrupting
        what is playing. One request per video, each after a random delay."""
        for video_id in _check_ids(video_ids, "add"):
            self._lounge._sleep(self._lounge._rng(*ADD_DELAY_RANGE))
            self._command("addVideo", {"videoId": video_id})


class Lounge:
    """Entry point. Holds the HTTP client and the identity of this remote.

    ``device_id``/``name`` are what the screen lists under "connected
    devices". ``client`` may be any :class:`httpx.Client`, which is also how
    tests inject a mock transport.
    """

    def __init__(
        self,
        *,
        name: str = "yttv",
        device_id: str = "remote",
        client: httpx.Client | None = None,
        timeout: float = 10.0,
        sleep: Callable[[float], None] = time.sleep,
        rng: Callable[[float, float], float] = random.uniform,
    ):
        self.name = name
        self.device_id = device_id
        self._own_client = client is None
        self._client = client or httpx.Client(headers=DEFAULT_HEADERS, timeout=timeout)
        self._sleep = sleep
        self._rng = rng

    def close(self) -> None:
        if self._own_client:
            self._client.close()

    def __enter__(self) -> Lounge:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- pairing and tokens -------------------------------------------------

    def pair(self, code: str) -> Screen:
        """Pair with a TV using the code shown under Settings > Link with TV
        code. Returns a screen with a fresh token."""
        digits = "".join(ch for ch in code if ch.isdigit())
        if not digits:
            raise PairingError(f"not a TV code: {code!r}")
        try:
            response = self._client.post(GET_SCREEN_URL, data={"pairing_code": digits})
        except httpx.HTTPError as exc:
            raise PairingError(str(exc)) from exc
        if response.status_code != 200:
            raise PairingError(f"HTTP {response.status_code}: {response.text[:200]}")
        try:
            screen = response.json()["screen"]
            return Screen(
                screen_id=str(screen["screenId"]),
                lounge_token=str(screen["loungeToken"]),
                expiration=_int_expiration(screen["expiration"]),
                name=str(screen.get("name", "")),
            )
        except (ValueError, KeyError, TypeError) as exc:
            raise PairingError(f"unexpected pairing response: {exc}") from exc

    def refresh(self, screen: Screen) -> Screen:
        """Fetch a new lounge token for ``screen`` from its durable id.

        This is the path that runs about once every two weeks and therefore
        never during ordinary development. Keep it covered by a test.
        """
        try:
            response = self._client.post(
                GET_LOUNGE_TOKEN_BATCH_URL, data={"screen_ids": screen.screen_id}
            )
        except httpx.HTTPError as exc:
            raise TokenError(str(exc)) from exc
        if response.status_code != 200:
            raise TokenError(f"HTTP {response.status_code}: {response.text[:200]}")
        try:
            entries = response.json()["screens"]
            match = next(e for e in entries if e.get("screenId") == screen.screen_id)
            return screen.with_token(
                lounge_token=str(match["loungeToken"]),
                expiration=_int_expiration(match["expiration"]),
            )
        except StopIteration:
            raise TokenError(f"no token returned for screen {screen.screen_id}") from None
        except (ValueError, KeyError, TypeError) as exc:
            raise TokenError(f"unexpected token response: {exc}") from exc

    # -- sessions -------------------------------------------------------------

    def connect(self, screen: Screen) -> Session:
        """Open a session with ``screen``. Does not change what the TV shows."""
        params = {
            "CVER": _CLIENT_VERSION,
            "RID": "1",
            "VER": _CHANNEL_VERSION,
            "app": _APP,
            "device": _DEVICE,
            "id": self.device_id,
            "loungeIdToken": screen.lounge_token,
            "name": self.name,
        }
        try:
            # Empty body on purpose; the endpoint still requires a
            # Content-Length header, which httpx always sends.
            response = self._client.post(BIND_URL, params=params, content=b"")
        except httpx.HTTPError as exc:
            raise SessionError(str(exc)) from exc
        if response.status_code != 200:
            raise SessionError(f"HTTP {response.status_code}: {response.text[:200]}")
        try:
            sid, gsessionid = session_ids(parse_frames(response.text))
        except ValueError as exc:
            raise SessionError(str(exc)) from exc
        return Session(self, screen, sid, gsessionid)

    # -- convenience ----------------------------------------------------------

    def play(
        self,
        screen: Screen,
        video_ids: Sequence[str],
        *,
        start_index: int = 0,
        start_seconds: int = 0,
    ) -> None:
        """Open a session and play. See :meth:`Session.play`.

        Arguments are validated before the session is opened, so a bad call
        never touches the network."""
        _check_play_args(video_ids, start_index)
        self.connect(screen).play(
            video_ids, start_index=start_index, start_seconds=start_seconds
        )

    def add(self, screen: Screen, video_ids: Sequence[str]) -> None:
        """Open a session and append to the queue. See :meth:`Session.add`."""
        _check_ids(video_ids, "add")
        self.connect(screen).add(video_ids)
