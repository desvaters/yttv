# yttv

Play YouTube videos on a TV from the command line or from Python.

Work in progress. See `NOTES.md` (local, not committed) for design notes.

Two import packages ship in this repository:

- `ytlounge`: client for the YouTube Lounge API, the protocol every path
  ends in. Kept free of any device or file-system knowledge so it can become
  its own distribution later.
- `yttv`: device backends (Cast, Apple TV, DIAL), the paired-screen cache
  and the `yttv` command.

## Development

```bash
pixi run test
```

`pixi run -e core314 test` runs the same tests on Python 3.14 without any
backend extra, which proves the core does not depend on them.
