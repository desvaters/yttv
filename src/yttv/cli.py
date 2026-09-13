"""Command line entry point for ``yttv``.

    yttv URL [URL ...]          play on the last used screen
    yttv -a URL [URL ...]       append to its queue
    yttv -d bedroom URL         pick a screen by name or address
    yttv -l                     list known screens
    yttv --pair 123456789       link a screen with the code the TV shows

Exit codes: 0 done, 1 something failed (message on stderr), 2 bad usage.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime
from importlib.metadata import PackageNotFoundError, version

from . import api
from .cache import Device


def _version() -> str:
    try:
        return version("yttv")
    except PackageNotFoundError:
        return "unknown"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="yttv",
        description="Play YouTube videos on a TV.",
        epilog=(
            "Videos are ids or URLs and are sent together in one go. "
            "Without -d the last used screen is taken."
        ),
    )
    parser.add_argument("videos", nargs="*", metavar="VIDEO", help="video id or YouTube URL")
    parser.add_argument(
        "-a", "--add", action="store_true", help="append to the queue instead of playing now"
    )
    parser.add_argument(
        "-d",
        "--device",
        metavar="NAME",
        help="screen to use: part of its name or address, or the start of its id",
    )
    parser.add_argument("-l", "--list", action="store_true", help="list known screens and exit")
    parser.add_argument(
        "--pair",
        metavar="CODE",
        help="link a screen with the code from Settings > Link with TV code, then use it",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=0,
        help="log what happens; -vv also logs HTTP requests, which contain the lounge token",
    )
    parser.add_argument("-V", "--version", action="version", version=f"%(prog)s {_version()}")
    return parser


def configure_logging(verbosity: int) -> None:
    """-v: our own INFO. -vv: DEBUG everywhere, including httpx's request
    lines. Those carry the lounge token in the URL, so httpx stays quiet
    unless explicitly asked."""
    if verbosity <= 0:
        return
    level = logging.DEBUG if verbosity > 1 else logging.INFO
    logging.basicConfig(format="%(name)s: %(message)s")
    # basicConfig only sets the level when it installs a handler; set it
    # regardless so a pre-configured root (pytest, an embedding app) works too.
    logging.getLogger().setLevel(level)
    if verbosity == 1:
        for name in ("httpx", "httpcore"):
            logging.getLogger(name).setLevel(logging.WARNING)


def format_device(device: Device, now_ms: int | None = None) -> str:
    marker = "*" if device.last_used else " "
    where = device.address or "paired by code"
    if device.backend:
        where = f"{where} ({device.backend})"
    expiry = datetime.fromtimestamp(device.screen.expiration / 1000).strftime("%Y-%m-%d")
    token = "token expired" if device.screen.is_expired(now_ms) else f"token until {expiry}"
    return f"{marker} {device.label:<24} {where:<28} {token}"


def _list(out) -> int:
    devices = api.devices()
    if not devices:
        print("No screens known yet. Pair one with --pair CODE.", file=out)
        return 0
    for device in devices:
        print(format_device(device), file=out)
    return 0


def run(argv: list[str], out=sys.stdout, err=sys.stderr) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    configure_logging(args.verbose)

    if args.list:
        return _list(out)
    if not args.pair and not args.videos:
        parser.print_usage(err)
        print("yttv: give a video to play, -l to list screens, or --pair CODE", file=err)
        return 2

    device: Device | str | None = args.device
    if args.pair:
        paired = api.pair(args.pair)
        print(f"Paired with {paired.label}.", file=out)
        device = paired
        if not args.videos:
            return 0

    used = api.cast(args.videos, queue=args.add, device=device)
    count = len(args.videos)
    noun = "video" if count == 1 else "videos"
    if args.add:
        print(f"Added {count} {noun} to the queue on {used.label}.", file=out)
    else:
        print(f"Playing {count} {noun} on {used.label}.", file=out)
    return 0


def main(argv: list[str] | None = None) -> int:
    """Run the CLI. Returns the process exit code."""
    args = sys.argv[1:] if argv is None else argv
    try:
        return run(args)
    except api.YttvError as exc:
        print(f"yttv: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
