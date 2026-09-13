# yttv

Play YouTube videos on a TV from the command line or from Python: Apple TV,
Cast TVs (Chromecast, Samsung Tizen) and DIAL devices (Fire TV, WebOS).

```bash
yttv https://youtu.be/dQw4w9WgXcQ        # play on the last used screen
yttv -a ID1 ID2                          # append to its queue, one call for all
yttv -d bedroom ID                       # pick a screen by name or address
yttv -l                                  # list known screens
```

## Install

```bash
pip install 'yttv[all]'        # every backend
pip install 'yttv[cast]'       # Cast TVs only
pip install 'yttv[appletv]'    # Apple TV only (needs Python < 3.14, see below)
pip install yttv               # DIAL and screens paired by code; only httpx
```

Or, in a [pixi](https://pixi.sh) project: `pixi add --pypi 'yttv[all]'`.

## Setting up a screen

Every TV needs one of these once. yttv remembers the result under
`~/.cache/yttv/devices.json` and picks the last used screen by default.

| TV | Once | Then |
|---|---|---|
| Apple TV | `yttv --appletv 192.168.1.5` and type the PIN it shows | `yttv URL` opens the app and plays |
| Chromecast, Samsung Tizen, other Cast TVs | `yttv --cast 192.168.1.6` | `yttv URL`; the first cast makes a Samsung ask you to accept Cast terms, once |
| Fire TV, WebOS, other DIAL devices | `yttv -s` finds them | `yttv URL` wakes the TV and starts the app |
| Anything with a YouTube app | `yttv --pair 123456789` with the code from *Settings › Link with TV code* | `yttv URL`, with the app already open |

Videos are ids or URLs in any of the usual forms (`watch?v=`, `youtu.be`,
`shorts`, `live`, `embed`, ...). A `t=` parameter becomes the start
position. Several videos are always sent together in one call: sending
them one after another scrambles the TV's queue.

## From Python

```python
import yttv

yttv.devices()                        # known screens, from the cache, no network
yttv.cast(["dQw4w9WgXcQ"])            # play now on the last used screen
yttv.cast([url1, url2], queue=True)   # append to its queue
yttv.cast([url], device="bedroom")    # pick a screen
yttv.pair("123 456 789")              # link a screen by TV code
yttv.add_device("cast", "192.168.1.6", cast_uuid="...")
yttv.discover()                       # DIAL search
```

Every failure is a subclass of `yttv.YttvError` with a message meant for
people, so a caller can show it as is. `cast()` takes up to `timeout`
seconds (default 90) for waking the TV and starting the app; that is how
long a sleeping Fire TV needs.

Screens paired with [ytcast](https://github.com/MarcoLucidi01/ytcast) are
taken over from `~/.cache/ytcast/ytcast.json` on the first run.

## How it works

Three ways to reach a TV, one protocol to drive it:

| Device | Finds and starts the app via | Plays via |
|---|---|---|
| Apple TV | [pyatv](https://github.com/postlund/pyatv), Companion protocol | Lounge |
| Cast TVs | [pychromecast](https://github.com/home-assistant-libs/pychromecast), the YouTube receiver's `mdx` channel | Lounge |
| DIAL devices | SSDP and the DIAL REST interface, built in | Lounge |

The Lounge API is the unofficial protocol behind the "Play on TV" button in
the phone app: pair with a screen, play a video, append to the queue. Each
backend's only job is to bring the YouTube app up and obtain the screen's
id; from there everything goes through `ytlounge`, the second package in
this repository. It knows nothing about devices, caches or files and is
kept extractable into its own distribution.

The Lounge API is not documented and can change at any time. If it does,
expect this to break the same way for every tool built on it.

### Python versions

The core runs on Python 3.11 and newer, 3.14 included. The Apple TV backend
depends on pyatv, which does not run on 3.14 yet; its extra is skipped
there and `yttv` says so when an Apple TV is used.

## When the search finds nothing

The DIAL search is a multicast packet; every device answers with a unicast
reply from its own address. A stateful firewall on your machine (ufw,
firewalld) does not connect that reply to the packet you sent and drops it
silently. Discovery then reports "no device found" although the packets are
on the wire.

```bash
yttv --doctor
```

sends the searches, counts the replies and, when nothing comes back, prints
the rule to check and the ufw line that fixes it. Cast TVs and Apple TV
never answer DIAL searches; that is expected, use `--cast` and `--appletv`
for those. `--doctor --host <ip>` probes a device directly and sidesteps
multicast.

## Origins

The Lounge protocol was reverse-engineered independently by several
people; nothing here is derived from their code, but their write-ups made
the protocol knowable. yttv learned it from Marco Lucidi's
[ytcast](https://github.com/MarcoLucidi01/ytcast) (Go), whose behaviour
served as the reference for verifying requests on the wire, and, through
it, from the sources ytcast itself credits:

- https://0x41.cf/automation/2021/03/02/google-assistant-youtube-smart-tvs.html
- https://github.com/thedroidgeek/youtube-cast-automation-api
- https://github.com/mutantmonkey/youtube-remote
- https://bugs.xdavidhu.me/google/2021/04/05/i-built-a-tv-that-plays-all-of-your-private-youtube-videos
- https://github.com/aykevl/plaincast
- https://github.com/ur1katz/casttube

The list of YouTube URL forms used in the tests comes from
[this gist](https://gist.github.com/rodrigoborgesdeoliveira/987683cfbfcc8d800192da1e73adc486).

## Development

```bash
pixi run test              # unit and fixture tests, Python 3.13 with all backends
pixi run -e core314 test   # the same on Python 3.14 without any backend extra
pixi run test-device       # needs a real TV on the network
pixi run check             # build wheel and sdist, validate the metadata
```

A test guards the boundary between `ytlounge` and `yttv`: importing the
core must not pull in any device module, at runtime or in the source.
