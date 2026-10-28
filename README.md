# Couchcast

Send the YouTube video you're watching in Firefox to the YouTube app on your TV with one click.
It picks up where you are and pauses the video in Firefox.

Chrome has a Cast button and Safari has AirPlay. Firefox has neither, so Couchcast fills the gap.
It needs no helper app, and it has no Chromecast or AirPlay dependency.

## How it works

The YouTube app on most TVs (Apple TV, Google TV, Fire TV, smart TVs, consoles) has
**Settings > Link with TV code**. The YouTube phone app uses that link to act as a remote. It runs
through YouTube's servers (the "Lounge" API), not your home network. Couchcast is another remote on
that same channel:

```
Firefox (Couchcast) --HTTPS--> www.youtube.com/api/lounge <--- YouTube app on the TV
```

The TV plays the video in its own YouTube app, so you get full quality, captions, and whatever
account the TV is signed in to.

## Using it

1. Install the extension. Its settings page opens on first install.
2. On the TV, open YouTube, then **Settings > Link with TV code**. Type that code into Couchcast.
3. On any YouTube video in Firefox, click the Couchcast toolbar button (or press
   <kbd>Alt</kbd>+<kbd>Shift</kbd>+<kbd>Y</kbd>). You can also right-click a YouTube link and choose
   **Cast to TV**.

The YouTube app has to be open on the TV. If it isn't, Couchcast says so instead of failing silently.

## Privacy

- Couchcast sends the video id, playlist id and start time you cast to YouTube, plus the TV code
  when you link a TV. Nothing goes anywhere else, and there is no analytics.
- Requests go out without your YouTube or Google cookies (`credentials: "omit"`). The TV link is the
  only credential, and it is stored in the extension's local storage.
- It reads the current position from the YouTube page only when you click Cast.

## Development

Requires [Bun](https://bun.sh) and Firefox 140 or newer.

```bash
bun install
bun test               # unit tests, including a fake Lounge server
bun run lint           # web-ext lint
bun run start          # launch a throwaway Firefox profile with the extension loaded
bun run build          # zip for addons.mozilla.org
```

`scripts/lounge-cli.js` drives the same Lounge client from a terminal against a real TV, which is
the fastest way to check protocol changes:

```bash
bun run lounge pair 123456789012
bun run lounge cast "https://www.youtube.com/watch?v=aqz-KE-bpKQ" --watch=15
```

Logs use a `[scope]` prefix (`[cast]`, `[lounge]`, `[options]`). To see them, open
about:debugging, then Inspect on Couchcast.

## Limitations

- The Lounge API is unofficial. YouTube could change it, though it has been stable for years and
  other open-source tools rely on it.
- It can't wake a sleeping TV or launch the YouTube app.
- YouTube only. Casting other sites to an Apple TV needs AirPlay, which needs a native helper app.

## Credits

Protocol behavior follows [pyytlounge](https://github.com/FabioGNR/pyytlounge) (GPL-3.0), the
library behind [iSponsorBlockTV](https://github.com/dmunozv04/iSponsorBlockTV).

## License

GPL-3.0-or-later. See [LICENSE](LICENSE).
