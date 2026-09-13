"""Every request is checked against the shape captured from the reference
implementation on the wire. Changing a parameter name here must be a
conscious decision backed by a new capture."""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest

from ytlounge import (
    CommandError,
    Lounge,
    PairingError,
    Screen,
    SessionError,
    TokenError,
)

FIXTURES = Path(__file__).parent / "fixtures"
SCREEN = Screen(
    screen_id="screen-id-1", lounge_token="TOKEN1", expiration=1_800_000_000_000, name="TV"
)


class Recorder:
    """Mock transport that records requests and answers from a script."""

    def __init__(self, responses: list[httpx.Response]):
        self.responses = responses
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.responses[len(self.requests) - 1]


def make_lounge(responses: list[httpx.Response], **kwargs) -> tuple[Lounge, Recorder]:
    recorder = Recorder(responses)
    client = httpx.Client(transport=httpx.MockTransport(recorder))
    options = {"sleep": lambda s: None, "rng": lambda a, b: a, **kwargs}
    lounge = Lounge(name="test-remote", client=client, **options)
    return lounge, recorder


def text(status: int, body: str) -> httpx.Response:
    return httpx.Response(status, text=body)


def form(request: httpx.Request) -> dict[str, str]:
    parsed = parse_qs(request.content.decode(), keep_blank_values=True)
    return {k: v[0] for k, v in parsed.items()}


SESSION_BODY = (FIXTURES / "bind_session.txt").read_text()
ACK_BODY = (FIXTURES / "bind_command.txt").read_text()


# -- connect --------------------------------------------------------------


def test_connect_sends_bind_exactly_like_reference() -> None:
    lounge, rec = make_lounge([text(200, SESSION_BODY)])
    session = lounge.connect(SCREEN)

    req = rec.requests[0]
    assert req.method == "POST"
    assert req.url.copy_with(query=None) == httpx.URL("https://www.youtube.com/api/lounge/bc/bind")
    assert dict(req.url.params) == {
        "CVER": "1",
        "RID": "1",
        "VER": "8",
        "app": "youtube-desktop",
        "device": "REMOTE_CONTROL",
        "id": "remote",
        "loungeIdToken": "TOKEN1",
        "name": "test-remote",
    }
    assert req.content == b""
    assert req.headers["content-length"] == "0"
    assert session.sid == "SIDSIDSIDSIDSIDS"
    assert session.gsessionid == "GSESSIONGSESSIONGSESSIONGSESSION"


def test_connect_uses_default_headers() -> None:
    lounge = Lounge()
    try:
        assert lounge._client.headers["origin"] == "https://www.youtube.com"
        assert "Mozilla" in lounge._client.headers["user-agent"]
    finally:
        lounge.close()


def test_connect_http_error_raises_session_error() -> None:
    lounge, _ = make_lounge([text(400, "bad")])
    with pytest.raises(SessionError, match="400"):
        lounge.connect(SCREEN)


def test_connect_garbage_body_raises_session_error() -> None:
    lounge, _ = make_lounge([text(200, "not a frame")])
    with pytest.raises(SessionError):
        lounge.connect(SCREEN)


# -- play -----------------------------------------------------------------


def test_play_sends_set_playlist_like_reference() -> None:
    lounge, rec = make_lounge([text(200, SESSION_BODY), text(200, ACK_BODY)])
    lounge.play(SCREEN, ["jNQXAC9IVRw"])

    req = rec.requests[1]
    assert dict(req.url.params) == {
        "CVER": "1",
        "RID": "2",
        "SID": "SIDSIDSIDSIDSIDS",
        "VER": "8",
        "gsessionid": "GSESSIONGSESSIONGSESSIONGSESSION",
        "loungeIdToken": "TOKEN1",
    }
    assert req.headers["content-type"] == "application/x-www-form-urlencoded"
    assert form(req) == {
        "count": "1",
        "req0__sc": "setPlaylist",
        "req0_currentIndex": "0",
        "req0_currentTime": "0",
        "req0_videoId": "jNQXAC9IVRw",
        "req0_videoIds": "jNQXAC9IVRw",
    }


def test_play_several_videos_in_one_request() -> None:
    lounge, rec = make_lounge([text(200, SESSION_BODY), text(200, ACK_BODY)])
    lounge.play(SCREEN, ["a", "b", "c"], start_index=1, start_seconds=42)
    fields = form(rec.requests[1])
    assert fields["req0_videoIds"] == "a,b,c"
    assert fields["req0_videoId"] == "b"
    assert fields["req0_currentIndex"] == "1"
    assert fields["req0_currentTime"] == "42"
    assert len(rec.requests) == 2


def test_play_rejects_empty_and_bad_index_before_connecting() -> None:
    lounge, rec = make_lounge([text(200, SESSION_BODY)])
    with pytest.raises(ValueError):
        lounge.play(SCREEN, [])
    with pytest.raises(ValueError):
        lounge.play(SCREEN, ["a"], start_index=1)
    with pytest.raises(ValueError):
        lounge.add(SCREEN, [])
    assert rec.requests == [], "no session is opened for an invalid call"


def test_play_http_error_raises_command_error() -> None:
    lounge, _ = make_lounge([text(200, SESSION_BODY), text(400, "nope")])
    with pytest.raises(CommandError, match="setPlaylist"):
        lounge.play(SCREEN, ["a"])


# -- add ------------------------------------------------------------------


def test_add_sends_add_video_per_id_with_delay_and_rising_rid() -> None:
    sleeps: list[float] = []
    rec = Recorder([text(200, SESSION_BODY), text(200, ACK_BODY), text(200, ACK_BODY)])
    lounge = Lounge(
        name="test-remote",
        client=httpx.Client(transport=httpx.MockTransport(rec)),
        sleep=sleeps.append,
        rng=lambda a, b: 2.5,
    )
    lounge.add(SCREEN, ["dQw4w9WgXcQ", "aqz-KE-bpKQ"])

    assert sleeps == [2.5, 2.5], "one delay before every addVideo"
    first, second = rec.requests[1], rec.requests[2]
    assert form(first) == {"count": "1", "req0__sc": "addVideo", "req0_videoId": "dQw4w9WgXcQ"}
    assert form(second) == {"count": "1", "req0__sc": "addVideo", "req0_videoId": "aqz-KE-bpKQ"}
    assert first.url.params["RID"] == "2"
    assert second.url.params["RID"] == "3"


def test_add_delay_range_is_two_to_five_seconds() -> None:
    seen: list[tuple[float, float]] = []

    def rng(a: float, b: float) -> float:
        seen.append((a, b))
        return a

    lounge, _ = make_lounge([text(200, SESSION_BODY), text(200, ACK_BODY)], rng=rng)
    lounge.add(SCREEN, ["a"])
    assert seen == [(2.0, 5.0)]


# -- pair / refresh ---------------------------------------------------------


def test_pair_posts_code_and_parses_string_expiration() -> None:
    body = json.dumps(
        {
            "screen": {
                "screenId": "screen-id-2",
                "loungeToken": "TOKEN2",
                "expiration": "1800000000000",
                "name": "Living room",
            }
        }
    )
    lounge, rec = make_lounge([text(200, body)])
    screen = lounge.pair("123 456 789")

    req = rec.requests[0]
    assert str(req.url) == "https://www.youtube.com/api/lounge/pairing/get_screen"
    assert form(req) == {"pairing_code": "123456789"}
    assert screen == Screen("screen-id-2", "TOKEN2", 1_800_000_000_000, "Living room")


def test_pair_rejects_bad_code_and_bad_response() -> None:
    lounge, _ = make_lounge([text(404, "")])
    with pytest.raises(PairingError):
        lounge.pair("abc")
    with pytest.raises(PairingError, match="404"):
        lounge.pair("123456789")


def test_refresh_posts_screen_id_and_parses_numeric_expiration() -> None:
    body = json.dumps(
        {
            "screens": [
                {"screenId": "other", "loungeToken": "X", "expiration": 1},
                {"screenId": "screen-id-1", "loungeToken": "TOKEN-NEW", "expiration": 1900000000000},
            ]
        }
    )
    lounge, rec = make_lounge([text(200, body)])
    refreshed = lounge.refresh(SCREEN)

    req = rec.requests[0]
    assert str(req.url) == "https://www.youtube.com/api/lounge/pairing/get_lounge_token_batch"
    assert form(req) == {"screen_ids": "screen-id-1"}
    assert refreshed.lounge_token == "TOKEN-NEW"
    assert refreshed.expiration == 1_900_000_000_000
    assert refreshed.screen_id == SCREEN.screen_id and refreshed.name == SCREEN.name


def test_refresh_without_matching_screen_raises() -> None:
    lounge, _ = make_lounge([text(200, json.dumps({"screens": []}))])
    with pytest.raises(TokenError, match="no token"):
        lounge.refresh(SCREEN)


def test_refresh_http_error_raises() -> None:
    lounge, _ = make_lounge([text(500, "")])
    with pytest.raises(TokenError, match="500"):
        lounge.refresh(SCREEN)


# -- Screen ---------------------------------------------------------------


def test_screen_expiry() -> None:
    s = Screen("id", "tok", expiration=1000)
    assert not s.is_expired(now_ms=999)
    assert s.is_expired(now_ms=1000)
    assert s.is_expired(now_ms=500, margin_ms=600)
