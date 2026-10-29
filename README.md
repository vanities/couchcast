# Couchcast

Send the YouTube video you're watching in Firefox to your TV with one click: a cast button in
YouTube's own player controls, a toolbar button, a right-click item, and
<kbd>Alt</kbd>+<kbd>Shift</kbd>+<kbd>Y</kbd>. The TV picks up where you are, and the video pauses
in Firefox.

Chrome has a Cast button and Safari has AirPlay. Firefox has neither, so Couchcast fills the gap.

## Two ways to play

| | No ads, straight to Apple TV | YouTube app on the TV |
|---|---|---|
| Ads | None | Whatever the TV's YouTube app shows (none with Premium) |
| TVs | Apple TV (AirPlay) | Any TV with the YouTube app and "Link with TV code" |
| Needs | The Couchcast helper app on this computer | Nothing else |
| Quality | Up to 1080p60 (H.264 over AirPlay) | Whatever the TV's YouTube app does |

Tested: no-ads mode from macOS to an Apple TV 4K (gen 2) on tvOS 27.0. YouTube-app mode has not been
tried on a real TV yet.

**No ads.** The helper resolves the video to YouTube's HLS stream with
[yt-dlp](https://github.com/yt-dlp/yt-dlp) and hands that stream to the Apple TV over AirPlay with
[pyatv](https://github.com/postlund/pyatv). The TV plays the video itself, so YouTube's ads, which
the YouTube player inserts, never run. A browser extension can't speak AirPlay, which is why this
mode needs a helper app; it talks to the extension through Firefox's native messaging.

**YouTube app.** The YouTube app on TVs has **Settings > Link with TV code**, the same link the
YouTube phone app uses as a remote. It runs through YouTube's servers (the "Lounge" API), so the
extension can use it on its own:

```
Firefox (Couchcast) --HTTPS--> www.youtube.com/api/lounge <--- YouTube app on the TV
```

## Setup

1. Install the extension. Its settings page opens on first install. Pick how it plays.
2. **No-ads mode:** set up the helper (needs [uv](https://docs.astral.sh/uv/) and
   [deno](https://deno.com), which yt-dlp uses for YouTube's player challenges):

   ```bash
   cd helper
   uv sync
   uv run couchcast-helper pair      # a PIN appears on the Apple TV
   uv run couchcast-helper install   # registers the helper with Firefox
   uv run couchcast-helper status    # check that everything is connected
   ```

3. **YouTube-app mode:** on the TV, open YouTube, then **Settings > Link with TV code**, and type
   that code into Couchcast's settings page.

## Privacy

- No-ads mode: the helper asks YouTube for the video's stream (like any player does) and sends that
  stream URL and a start time to your Apple TV on your local network.
- YouTube-app mode: Couchcast sends the video id, playlist id and start time to YouTube, plus the
  TV code when you link a TV. Requests go out without your YouTube or Google cookies.
- Nothing goes anywhere else. There is no analytics.
- It reads the current position from the YouTube page only when you cast.

## Development

Requires [Bun](https://bun.sh), Firefox 140 or newer, and uv for the helper.

```bash
bun install
bun test               # extension unit tests, including a fake Lounge server
bun run lint           # web-ext lint
bun run start          # throwaway Firefox profile with the extension loaded
bun run build          # zip for addons.mozilla.org
(cd helper && uv run pytest)

# Drive each transport from a terminal against a real TV:
(cd helper && uv run couchcast-helper play "https://www.youtube.com/watch?v=aqz-KE-bpKQ" --at 30)
bun run lounge pair 123456789012
bun run lounge cast "https://www.youtube.com/watch?v=aqz-KE-bpKQ" --watch=15
```

Extension checks without YouTube or the TV: a real Firefox (driven by Selenium) with a
pre-configured copy of the extension opens a stand-in YouTube watch page that Firefox's automation
protocol serves locally. It checks what a cast sends (video, position, speed), switching videos
inside the page, reloading the extension with the tab open, and catching a paused tab up with the
TV. About 35 seconds, muted and headless.

```bash
uv run --project e2e python e2e/fake_youtube.py            # or name scenarios: cast switch catch-up reload
```

End-to-end test: a real Firefox (driven by Selenium) with a pre-configured copy of the extension
opens a YouTube video, clicks the in-player cast button, and checks what reached the helper and,
from the TV's own events, that the session holds and the position carried over. It plays on the
TV unless you pass `--dry-run`. The test Firefox is muted and blocks ads with uBlock Origin, in a
profile kept at `.dev/e2e-profile` so uBlock's filter lists stay current.

```bash
uv run --project e2e python e2e/run.py                    # add --no-pause, --watch 60, --url ...
uv run --project e2e python e2e/run.py --next --dry-run   # switch videos in the page first; TV untouched
```

Every run is still an automated YouTube visit, dry run included. Runs close together trip YouTube's
bot check ("Sign in to confirm you're not a bot"), which then blocks the helper's yt-dlp on the
same network too, so space runs out.

Logs: the extension uses `[cast]`, `[lounge]`, `[options]` and `[couchcast]` prefixes (about:debugging,
Inspect). The helper logs to `~/.config/couchcast/helper.log`; set `"debug": true` in
`~/.config/couchcast/config.json` (or `COUCHCAST_DEBUG=1`) to include pyatv's protocol traffic.

## Limitations

- No-ads mode: the computer has to stay awake while the video plays, because AirPlay video stops
  when the sender disconnects (the helper holds off idle sleep, but closing a laptop lid still ends
  it). On macOS the player runs as its own launchd job: the Apple TV drops AirPlay sessions from
  processes macOS attributes to Firefox a few seconds in.
- pyatv is pinned to an unreleased fix for tvOS 26 and newer
  ([postlund/pyatv#2906](https://github.com/postlund/pyatv/issues/2906)), with srptools held below
  2.0 ([postlund/pyatv#2934](https://github.com/postlund/pyatv/issues/2934)). The helper also
  wraps pyatv's AirPlay 2 commands to start at the right position and speed and to read the TV's
  position from its events. When YouTube changes things, `uv lock --upgrade-package yt-dlp` usually
  fixes it.
- YouTube-app mode: the Lounge API is unofficial. It can't wake the TV or open the YouTube app.
- YouTube only, for now.

## Credits

- [pyytlounge](https://github.com/FabioGNR/pyytlounge) (GPL-3.0): Lounge protocol behavior.
- [pyatv](https://github.com/postlund/pyatv) (MIT) and [yt-dlp](https://github.com/yt-dlp/yt-dlp)
  (Unlicense): the helper.
- Cast icon from [Material Design Icons](https://github.com/google/material-design-icons) (Apache-2.0).

## License

GPL-3.0-or-later. See [LICENSE](LICENSE).
