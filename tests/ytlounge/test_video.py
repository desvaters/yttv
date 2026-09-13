from pathlib import Path

import pytest

from ytlounge.video import Video, parse_start_time, parse_video

ID = "dQw4w9WgXcQ"


@pytest.mark.parametrize(
    "text",
    [
        ID,
        f"  {ID}\n",
        f"https://www.youtube.com/watch?v={ID}",
        f"http://youtube.com/watch?v={ID}",
        f"youtube.com/watch?v={ID}",
        f"https://m.youtube.com/watch?v={ID}",
        f"https://music.youtube.com/watch?v={ID}&list=RDAMVM{ID}",
        f"https://www.youtube.com/watch?feature=share&v={ID}",
        f"https://www.youtube.com/watch?v={ID}&list=PL123&index=4",
        f"https://youtu.be/{ID}",
        f"https://youtu.be/{ID}?si=abcdef",
        f"https://www.youtube.com/embed/{ID}",
        f"https://www.youtube-nocookie.com/embed/{ID}?rel=0",
        f"https://www.youtube.com/v/{ID}?version=3",
        f"https://www.youtube.com/e/{ID}",
        f"https://www.youtube.com/shorts/{ID}",
        f"https://www.youtube.com/live/{ID}?feature=share",
        f"https://www.youtube.com/watch/{ID}",
        f"https://www.youtube.com/watch?vi={ID}",
        f"https://www.youtube.com/attribution_link?a=abc&u=%2Fwatch%3Fv%3D{ID}%26feature%3Dshare",
    ],
)
def test_parse_finds_the_id(text: str) -> None:
    assert parse_video(text).id == ID


@pytest.mark.parametrize(
    "text",
    [
        "",
        "not a video",
        "dQw4w9WgXc",  # ten characters
        "dQw4w9WgXcQQ",  # twelve
        "https://example.com/watch?v=" + ID,
        "https://vimeo.com/123456",
        "https://www.youtube.com/",
        "https://www.youtube.com/playlist?list=PL123",
        "https://www.youtube.com/channel/UCabc",
        "https://www.youtube.com/watch?v=short",
        "https://youtu.be/",
    ],
)
def test_parse_rejects(text: str) -> None:
    with pytest.raises(ValueError):
        parse_video(text)


@pytest.mark.parametrize(
    ("text", "seconds"),
    [
        (f"https://www.youtube.com/watch?v={ID}&t=90", 90),
        (f"https://www.youtube.com/watch?v={ID}&t=90s", 90),
        (f"https://youtu.be/{ID}?t=1m30s", 90),
        (f"https://youtu.be/{ID}?t=1h2m3s", 3723),
        (f"https://www.youtube.com/embed/{ID}?start=42", 42),
        (f"https://www.youtube.com/watch?v={ID}&t=nonsense", 0),
        (f"https://www.youtube.com/watch?v={ID}", 0),
    ],
)
def test_parse_start_time_from_url(text: str, seconds: int) -> None:
    assert parse_video(text) == Video(ID, seconds)


@pytest.mark.parametrize(
    ("text", "seconds"),
    [("0", 0), ("90", 90), ("90s", 90), ("2m", 120), ("1m30s", 90), ("1h", 3600), ("1h2m3s", 3723), ("1H2M", 3720)],
)
def test_parse_start_time(text: str, seconds: int) -> None:
    assert parse_start_time(text) == seconds


@pytest.mark.parametrize("text", ["", "m", "1x", "1s2m", "-5", "1.5m", "abc"])
def test_parse_start_time_rejects(text: str) -> None:
    with pytest.raises(ValueError):
        parse_start_time(text)


def _gist_cases() -> list[tuple[str, str]]:
    path = Path(__file__).parent / "fixtures" / "youtube_urls.txt"
    cases = []
    for line in path.read_text().splitlines():
        if line and not line.startswith("#"):
            video_id, url = line.split(" ", 1)
            cases.append((video_id, url))
    return cases


@pytest.mark.parametrize(("video_id", "url"), _gist_cases())
def test_every_url_form_from_the_gist(video_id: str, url: str) -> None:
    assert parse_video(url).id == video_id


def test_fragment_start_time() -> None:
    assert parse_video("https://www.youtube.com/watch?v=0zM3nApSvMg#t=0m10s") == Video("0zM3nApSvMg", 10)
