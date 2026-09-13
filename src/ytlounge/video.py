"""Turning what a user pastes into a video id.

Accepts a bare 11-character id or any of the common YouTube URL forms
(watch, youtu.be, embed, shorts, live, mobile, music, nocookie,
attribution_link). A ``t=`` parameter becomes the start position.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import parse_qs, unquote, urlsplit

VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")

_YOUTUBE_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "gaming.youtube.com",
    "www.youtube-nocookie.com",
    "youtube-nocookie.com",
}
_SHORT_HOSTS = {"youtu.be", "www.youtu.be"}

# Path prefixes after which the next segment is the video id.
_ID_PATH_PREFIXES = ("embed", "v", "e", "shorts", "live", "watch")

_DURATION = re.compile(
    r"^(?:(?P<h>\d+)h)?(?:(?P<m>\d+)m)?(?:(?P<s>\d+)s?)?$"
)


@dataclass(frozen=True)
class Video:
    id: str
    start_seconds: int = 0


def parse_start_time(text: str) -> int:
    """Seconds from a ``t=`` value: ``90``, ``90s``, ``1m30s``, ``1h2m3s``.

    Raises ``ValueError`` for anything else.
    """
    text = text.strip().lower()
    match = _DURATION.match(text)
    if not text or not match or not any(match.group(g) for g in ("h", "m", "s")):
        raise ValueError(f"not a start time: {text!r}")
    hours = int(match.group("h") or 0)
    minutes = int(match.group("m") or 0)
    seconds = int(match.group("s") or 0)
    return hours * 3600 + minutes * 60 + seconds


def parse_video(text: str) -> Video:
    """Extract the video id (and start time) from an id or URL.

    Raises ``ValueError`` when no video id can be found.
    """
    text = text.strip()
    if VIDEO_ID.match(text):
        return Video(text)

    url = urlsplit(text if "://" in text else "https://" + text)
    host = (url.hostname or "").lower()
    query = parse_qs(url.query, keep_blank_values=False)
    segments = [s for s in url.path.split("/") if s]

    video_id: str | None = None
    if host in _SHORT_HOSTS:
        video_id = segments[0] if segments else None
    elif host in _YOUTUBE_HOSTS:
        if segments and segments[0] == "attribution_link" and query.get("u"):
            # The real URL is nested, percent-encoded, in the `u` parameter.
            return parse_video("https://www.youtube.com" + unquote(query["u"][0]))
        if query.get("v"):
            video_id = query["v"][0]
        elif query.get("vi"):
            video_id = query["vi"][0]
        elif len(segments) >= 2 and segments[0] in _ID_PATH_PREFIXES:
            video_id = segments[1]
    else:
        raise ValueError(f"not a YouTube URL: {text!r}")

    if video_id is None or not VIDEO_ID.match(video_id):
        raise ValueError(f"no video id in {text!r}")

    start = 0
    for key in ("t", "start"):
        if query.get(key):
            try:
                start = parse_start_time(query[key][0])
            except ValueError:
                start = 0
            break
    return Video(video_id, start)
