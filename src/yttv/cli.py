"""Command line entry point for ``yttv``.

    yttv URL [URL ...]          play on the last used screen
    yttv -a URL [URL ...]       append to its queue
    yttv -d bedroom URL         pick a screen by name or address
    yttv -l                     list known screens
    yttv -s                     search for DIAL devices (Fire TV, WebOS)
    yttv --pair 123456789       link a screen with the code the TV shows
    yttv --appletv 192.168.1.5  attach an Apple TV to the screen
    yttv --doctor               why does the search find nothing?

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
        "-s", "--search", action="store_true", help="search the network for DIAL devices, remember them, and list"
    )
    parser.add_argument(
        "-t", "--timeout", type=float, default=4.0, metavar="SECONDS", help="how long to wait for search replies (default 4)"
    )
    parser.add_argument(
        "-i", "--interface", metavar="IP", help="local address to search from, for machines with several networks"
    )
    parser.add_argument(
        "--host", action="append", default=[], metavar="IP", help="also probe this device directly (repeatable); sidesteps multicast"
    )
    parser.add_argument(
        "--doctor", action="store_true", help="diagnose why discovery finds nothing (firewall, multicast) and exit"
    )
    parser.add_argument(
        "--pair",
        metavar="CODE",
        help="link a screen with the code from Settings > Link with TV code, then use it",
    )
    parser.add_argument(
        "--appletv",
        metavar="HOST",
        help="attach an Apple TV at HOST to the screen (pairs Companion with its PIN if needed), "
        "so yttv can bring the YouTube app to the front before playing",
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
    if device.screen is None:
        token = "no screen id yet"
    else:
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


def _attach_appletv(host: str, device: Device | str | None, out) -> Device:
    """Pair Companion if needed, then record the Apple TV on the screen."""
    from .backends import BackendUnavailable, get_launcher

    try:
        get_launcher("appletv")
    except BackendUnavailable as exc:
        raise api.YttvError(str(exc)) from exc
    from .backends import appletv

    def ask_pin(name: str) -> str:
        return input(f"PIN shown on {name}: ")

    try:
        name = appletv.ensure_paired(host, ask_pin)
    except appletv.AppleTVError as exc:
        raise api.CastError(f"Apple TV at {host}: {exc}") from exc
    attached = api.attach(device, "appletv", host, apple_name=name)
    print(f"{attached.label} is reached through {name} at {host}.", file=out)
    return attached


def run(argv: list[str], out=sys.stdout, err=sys.stderr) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    configure_logging(args.verbose)

    if args.doctor:
        from . import doctor

        return doctor.run(out, timeout=args.timeout, local_address=args.interface, hosts=args.host)
    if args.search:
        found = api.discover(timeout=args.timeout, local_address=args.interface, hosts=args.host)
        if not found:
            print("No DIAL device answered. Try --doctor.", file=out)
        else:
            for device in found:
                print(format_device(device), file=out)
        if not args.videos:
            return 0
    if args.list:
        return _list(out)
    if not args.pair and not args.appletv and not args.videos:
        parser.print_usage(err)
        print("yttv: give a video to play, -l to list screens, or --pair CODE", file=err)
        return 2

    device: Device | str | None = args.device
    if args.pair:
        paired = api.pair(args.pair)
        print(f"Paired with {paired.label}.", file=out)
        device = paired
    if args.appletv:
        device = _attach_appletv(args.appletv, device, out)
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
