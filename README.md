# yttv

Play YouTube videos on a TV from the command line or from Python.

Work in progress. See `NOTES.md` (local, not committed) for design notes.

## Usage

```bash
yttv https://youtu.be/dQw4w9WgXcQ        # play on the last used screen
yttv -a ID1 ID2                          # append to its queue, one call for all
yttv -d bedroom ID                       # pick a screen by name or address
yttv -l                                  # list known screens
yttv -s                                  # search for DIAL devices (Fire TV, WebOS)
yttv --pair 123456789                    # link a screen with the TV's code
yttv --appletv 192.168.1.5               # attach an Apple TV so yttv can open the app
yttv --doctor                            # why does the search find nothing?
```

Screens paired by code need the YouTube app open on the TV. With a backend
attached (`--appletv`, or found by `-s`) yttv wakes the TV and brings the
app to the front first.

## When the search finds nothing

The search is a multicast packet; every device answers with a unicast
reply from its own address. A stateful firewall on your machine (ufw,
firewalld) does not connect that reply to the packet you sent and drops it
silently. `yttv --doctor` sends the searches, counts the replies and, when
nothing comes back, prints the rule to check and the ufw line that fixes
it. Cast TVs and Apple TV never answer DIAL searches; that is expected.

Videos are ids or URLs and are always sent together. Sending them one after
another scrambles the TV's queue.

From Python:

```python
import yttv
yttv.devices()                        # paired screens, from the cache, no network
yttv.cast(["dQw4w9WgXcQ"])            # play now
yttv.cast([url1, url2], queue=True)   # append
yttv.pair("123 456 789")
```

Errors are subclasses of `yttv.YttvError` with messages meant for people.

Screens paired with ytcast are taken over from `~/.cache/ytcast/ytcast.json`
on the first run; yttv keeps its own file under `~/.cache/yttv/` afterwards.

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
backend extra, which proves the core does not depend on them. Tests that
need a real TV are opt-in: `pixi run test-device`.
