# Couchcast

Send the YouTube video you're watching in Firefox to your TV with one click: a cast button in
YouTube's own player controls, a toolbar button, a right-click item, and
<kbd>Alt</kbd>+<kbd>Shift</kbd>+<kbd>Y</kbd>. The TV picks up where you are, and the video pauses
in Firefox.

Chrome has a Cast button and Safari has AirPlay. Firefox has neither, so Couchcast fills the gap.

**Get it:** [Couchcast on addons.mozilla.org](https://addons.mozilla.org/firefox/addon/couchcast/)
(submitted for Mozilla's review; the page goes live once it's approved), or the signed `.xpi` from the
[latest release](https://github.com/vanities/couchcast/releases/latest). Website:
[am2.biz/couchcast](https://am2.biz/couchcast), with [support](https://am2.biz/couchcast/support)
and the [privacy policy](https://am2.biz/couchcast/privacy). Source:
[github.com/vanities/couchcast](https://github.com/vanities/couchcast). Problems or ideas:
[issues](https://github.com/vanities/couchcast/issues).

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

1. Install the extension (desktop Firefox 142 or newer):
   - from [addons.mozilla.org](https://addons.mozilla.org/firefox/addon/couchcast/), once Mozilla's
     review is done, or
   - from the [latest release](https://github.com/vanities/couchcast/releases/latest): download
     `couchcast-<version>.xpi` (signed by Mozilla) and drag it into a Firefox window.

   Its settings page opens on first install. Pick how it plays.
2. **No-ads mode:** install the helper app (needs [uv](https://docs.astral.sh/uv/) and
   [deno](https://deno.com), which yt-dlp uses for YouTube's player challenges), then pair it:

   ```bash
   uv tool install "git+https://github.com/vanities/couchcast#subdirectory=helper"
   couchcast-helper pair        # a PIN appears on the Apple TV
   couchcast-helper install     # registers the helper with Firefox
   couchcast-helper status      # check that everything is connected
   ```

   From a clone you can also run it in place: `cd helper && uv sync && uv run couchcast-helper
   install`, or `uv tool install ./helper`. Either way, `install` points Firefox at the Python it
   ran with.

3. **YouTube-app mode:** on the TV, open YouTube, then **Settings > Link with TV code**, and type
   that code into Couchcast's settings page.

## Privacy

Couchcast has no analytics and no server of its own; it talks to YouTube or the helper only to do
what you ask. Firefox's install prompt lists this as **browsing activity**, Mozilla's category for
the address of a page you're on. The full policy is in [amo/privacy-policy.md](amo/privacy-policy.md).

- No-ads mode: the extension hands the video id, position and speed to the helper on your
  computer. The helper asks YouTube for the video's stream (like any player does) and sends that
  stream URL and a start time to your Apple TV on your local network.
- If you turn on **use my Firefox YouTube login** (off by default) and YouTube's bot check blocks
  the helper, the helper asks once more with your YouTube cookies from Firefox.
- YouTube-app mode: Couchcast sends the video id, playlist id and start time to YouTube, plus the
  TV code when you link a TV. Requests go out without your YouTube or Google cookies.
- Nothing goes anywhere else. There is no analytics.
- It reads the current position from the YouTube page only when you cast.

## Development

Requires [Bun](https://bun.sh), Firefox 142 or newer, and uv for the helper.

```bash
bun install
bun test               # extension unit tests, including a fake Lounge server
bun run lint           # web-ext lint
bun run start          # throwaway Firefox profile with the extension loaded
bun run build          # zip for addons.mozilla.org
bun run sign           # Mozilla-signed .xpi, see Publishing
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

What the extension costs inside Firefox, on the same stand-in page with a YouTube-sized DOM that
changes every frame: the tab's CPU with Couchcast on and off in alternating windows, the
extension's own upkeep timer, and whether Firefox suspends the idle background page. About 8
minutes.

```bash
uv run --project e2e python e2e/measure_extension.py       # --pairs 5 --seconds 65
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
Inspect). On YouTube pages it reports what keeping its button in place cost, once a minute at
debug level. The helper logs to `~/.config/couchcast/helper.log`; set `"debug": true` in
`~/.config/couchcast/config.json` (or `COUCHCAST_DEBUG=1`) to include pyatv's protocol traffic.
Every helper call logs its process's CPU and peak memory, and a player logs both every 10 minutes
and when it exits:

```bash
grep -E '\[player\] (usage|exiting)|\[host\] reply' ~/.config/couchcast/helper.log | tail
```

## Publishing

Release Firefox only installs extensions Mozilla has signed. Signing goes through
addons.mozilla.org (AMO) in one of two channels:

- **Unlisted:** Mozilla checks and signs the extension for you to hand out yourself. It isn't
  public. `bun run sign` does this and saves the signed `.xpi` in `web-ext-artifacts/`.
- **Listed:** the public page, [addons.mozilla.org/firefox/addon/couchcast](https://addons.mozilla.org/firefox/addon/couchcast/),
  reviewed by Mozilla, with automatic updates. `bun run sign listed` submits a version. The
  listing's text, category, license and reviewer notes are in `amo/listing.json` (desktop only,
  since Couchcast doesn't support Firefox for Android), and its privacy policy is
  `amo/privacy-policy.md`. `web-ext` doesn't send the homepage and support links, privacy policy,
  icon or screenshots, or any later edit to the summary and description; `scripts/amo_listing.py`
  sets those (`uv run --with requests python scripts/amo_listing.py links text privacy icon
  screenshots`).

One-time setup:

1. Sign in to the [AMO Developer Hub](https://addons.mozilla.org/developers/) with a Mozilla
   account, and accept the Firefox Add-on Distribution Agreement if it asks.
2. Generate API credentials on the [API keys page](https://addons.mozilla.org/developers/addon/api/key/)
   and paste the **JWT issuer** and **JWT secret** into `.dev/amo.env` (git-ignored, owner-only):

   ```
   WEB_EXT_API_KEY=user:12345678:123
   WEB_EXT_API_SECRET=...
   ```

For each release, raise `version` in `extension/manifest.json` (every upload needs a new version),
then run `bun run sign`.

Things that can't change later or that reviewers look at:

- The add-on ID, `couchcast@vanities`, is permanent once uploaded, in either channel.
- `data_collection_permissions` declares `browsingActivity` because a cast sends the page's video
  out of the browser. Mozilla counts data sent to a native app too
  ([add-on policies](https://extensionworkshop.com/documentation/publish/add-on-policies/),
  [data collection consent](https://extensionworkshop.com/documentation/develop/firefox-builtin-data-consent/)).
- The minimum is Firefox 142, the first version whose Android build understands
  `data_collection_permissions`; with 140 the validator warns even though Couchcast is desktop-only.

More: [`web-ext sign`](https://extensionworkshop.com/documentation/develop/web-ext-command-reference/).

## Limitations

- No-ads mode: the computer has to stay awake while the video plays, because AirPlay video stops
  when the sender disconnects (the helper holds off idle sleep, but closing a laptop lid still ends
  it). A video left paused on the TV for 30 minutes ends the session, so the computer can sleep
  again; cast again to pick it back up. On macOS the player runs as its own launchd job: the Apple
  TV drops AirPlay sessions from processes macOS attributes to Firefox a few seconds in.
- pyatv is pinned to an unreleased fix for tvOS 26 and newer
  ([postlund/pyatv#2906](https://github.com/postlund/pyatv/issues/2906)), with srptools held below
  2.0 ([postlund/pyatv#2934](https://github.com/postlund/pyatv/issues/2934)). The helper also
  wraps pyatv's AirPlay 2 commands to start at the right position and speed, to read the TV's
  position from its events, and to notice when the TV leaves the video
  ([findings shared upstream](https://github.com/postlund/pyatv/pull/2846#issuecomment-6485722270)).
  When YouTube changes things, `uv lock --upgrade-package yt-dlp` usually fixes it.
- YouTube can stop handing streams to the helper with its bot check ("Sign in to confirm you're
  not a bot"). It happened here after many automated test visits from one network. The settings
  page has an opt-in to retry with your Firefox YouTube login.
- YouTube-app mode: the Lounge API is unofficial. It can't wake the TV or open the YouTube app.
- YouTube only, for now.

## Credits

- [pyytlounge](https://github.com/FabioGNR/pyytlounge) (GPL-3.0): Lounge protocol behavior.
- [pyatv](https://github.com/postlund/pyatv) (MIT) and [yt-dlp](https://github.com/yt-dlp/yt-dlp)
  (Unlicense): the helper.
- Cast icon from [Material Design Icons](https://github.com/google/material-design-icons) (Apache-2.0).

## License

GPL-3.0-only. See [LICENSE](LICENSE). The YouTube Lounge client is ported from
[pyytlounge](https://github.com/FabioGNR/pyytlounge), which is GPL-3.0 without an "or later" grant.
