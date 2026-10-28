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

Logs: the extension uses `[cast]`, `[lounge]`, `[options]` and `[couchcast]` prefixes (about:debugging,
Inspect). The helper logs to `~/.config/couchcast/helper.log`.

## Limitations

- No-ads mode: the computer has to stay awake while the video plays, because AirPlay video stops
  when the sender disconnects (the helper holds off idle sleep, but closing a laptop lid still ends
  it). pyatv is pinned to an unreleased fix for tvOS 26 and newer
  ([postlund/pyatv#2906](https://github.com/postlund/pyatv/issues/2906)). When YouTube changes
  things, `uv lock --upgrade-package yt-dlp` usually fixes it.
- YouTube-app mode: the Lounge API is unofficial. It can't wake the TV or open the YouTube app.
- YouTube only, for now.

## Credits

- [pyytlounge](https://github.com/FabioGNR/pyytlounge) (GPL-3.0): Lounge protocol behavior.
- [pyatv](https://github.com/postlund/pyatv) (MIT) and [yt-dlp](https://github.com/yt-dlp/yt-dlp)
  (Unlicense): the helper.
- Cast icon from [Material Design Icons](https://github.com/google/material-design-icons) (Apache-2.0).

## License

GPL-3.0-or-later. See [LICENSE](LICENSE).
