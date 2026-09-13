"""Command line entry point for ``yttv``."""

from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> int:
    """Run the CLI. Returns the process exit code."""
    args = sys.argv[1:] if argv is None else argv
    print("yttv: not implemented yet", file=sys.stderr)
    return 2 if args else 0


if __name__ == "__main__":
    sys.exit(main())
