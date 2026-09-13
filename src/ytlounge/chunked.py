"""Parser for the framed responses of ``/api/lounge/bc/bind``.

The body is a sequence of frames. Each frame is a decimal length on its own
line followed by that many characters of JSON: an array of numbered messages
``[index, [name, payload...]]``. The session handshake answers with the
session id as ``["c", sid, ...]`` and the gsession id as ``["S", gsessionid]``.
"""

from __future__ import annotations

import json
from typing import Any

Message = list[Any]


def parse_frames(body: str) -> list[Message]:
    """Return every message from every frame in ``body``, in order.

    Raises ``ValueError`` on a malformed frame.
    """
    messages: list[Message] = []
    pos = 0
    while pos < len(body):
        newline = body.find("\n", pos)
        if newline == -1:
            raise ValueError(f"missing length line at offset {pos}")
        length_text = body[pos:newline].strip()
        if not length_text:
            pos = newline + 1
            continue
        try:
            length = int(length_text)
        except ValueError as exc:
            raise ValueError(f"bad frame length {length_text!r}") from exc
        start = newline + 1
        payload = body[start : start + length]
        try:
            frame = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise ValueError(f"bad frame JSON at offset {start}: {exc}") from exc
        if not isinstance(frame, list):
            raise ValueError("frame is not a JSON array")
        messages.extend(frame)
        pos = start + length
    return messages


def find_event(messages: list[Message], name: str) -> Message | None:
    """Return the first ``[name, payload...]`` event with the given name."""
    for message in messages:
        if (
            isinstance(message, list)
            and len(message) >= 2
            and isinstance(message[1], list)
            and message[1]
            and message[1][0] == name
        ):
            return message[1]
    return None


def session_ids(messages: list[Message]) -> tuple[str, str]:
    """Extract ``(sid, gsessionid)`` from a bind handshake.

    Raises ``ValueError`` when either is missing.
    """
    c_event = find_event(messages, "c")
    s_event = find_event(messages, "S")
    if c_event is None or len(c_event) < 2 or not c_event[1]:
        raise ValueError("no session id ('c' event) in bind response")
    if s_event is None or len(s_event) < 2 or not s_event[1]:
        raise ValueError("no gsessionid ('S' event) in bind response")
    return str(c_event[1]), str(s_event[1])
