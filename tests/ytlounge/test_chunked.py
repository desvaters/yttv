from pathlib import Path

import pytest

from ytlounge.chunked import find_event, parse_frames, session_ids

FIXTURES = Path(__file__).parent / "fixtures"


def test_parse_session_handshake() -> None:
    body = (FIXTURES / "bind_session.txt").read_text()
    messages = parse_frames(body)
    assert [m[0] for m in messages] == [0, 1, 2, 3, 4, 5]
    assert session_ids(messages) == (
        "SIDSIDSIDSIDSIDS",
        "GSESSIONGSESSIONGSESSIONGSESSION",
    )
    status = find_event(messages, "loungeStatus")
    assert status is not None and status[1]["queueId"].startswith("QUEUE")


def test_parse_command_ack() -> None:
    body = (FIXTURES / "bind_command.txt").read_text()
    assert parse_frames(body) == [0, -1, 0]


def frame(payload: str) -> str:
    return f"{len(payload)}\n{payload}"


def test_parse_multiple_frames() -> None:
    body = frame('[[0,["a"]]]') + "\n" + frame('[[1,["b"]]]')
    assert parse_frames(body) == [[0, ["a"]], [1, ["b"]]]


def test_parse_ignores_blank_lines_between_frames() -> None:
    body = frame('[[0,["a"]]]') + "\n\n" + frame('[[1,["b"]]]') + "\n"
    assert parse_frames(body) == [[0, ["a"]], [1, ["b"]]]


@pytest.mark.parametrize(
    "body",
    ["nope\n[]", "5\n[1,2", "3\n123", "[[0]]"],
)
def test_parse_rejects_malformed(body: str) -> None:
    with pytest.raises(ValueError):
        parse_frames(body)


def test_session_ids_missing() -> None:
    with pytest.raises(ValueError):
        session_ids([[0, ["c", "sid", "", 8]]])
    with pytest.raises(ValueError):
        session_ids([[1, ["S", "g"]]])
