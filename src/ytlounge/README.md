# ytlounge

Client for the YouTube Lounge API, the protocol behind "play on TV".

This package lives inside the `yttv` repository for now and ships with the
`yttv` distribution. It is written to be extracted into its own distribution
(`ytlounge` on PyPI) once there is a second consumer: no import from `yttv`,
no knowledge of devices, caches or file paths. Interface: a `screen_id` in,
play and queue out.
