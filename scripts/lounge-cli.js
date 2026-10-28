#!/usr/bin/env bun
// Drive the extension's lounge client from a terminal against a real TV, so protocol changes can
// be checked without reloading the extension. State lives in .dev/ (gitignored, mode 600).
//
//   bun scripts/lounge-cli.js pair <tv code>
//   bun scripts/lounge-cli.js status
//   bun scripts/lounge-cli.js cast <youtube url> [--watch=15] [--moz-origin]

import { chmod, mkdir, readFile, writeFile } from 'node:fs/promises';
import { castVideo, disconnect, getAvailability, pairWithCode, refreshToken, watchEvents } from '../extension/lib/lounge.js';
import { parseYouTubeUrl } from '../extension/lib/youtube-url.js';

const STATE_DIR = new URL('../.dev/', import.meta.url);
const STATE_FILE = new URL('state.json', STATE_DIR);

const [command, ...rest] = process.argv.slice(2);
const flags = Object.fromEntries(
  rest.filter((a) => a.startsWith('--')).map((a) => {
    const [key, value = 'true'] = a.slice(2).split('=');
    return [key, value];
  }),
);
const args = rest.filter((a) => !a.startsWith('--'));

if (flags['moz-origin']) {
  // Make every request look like it came from an installed extension.
  const realFetch = globalThis.fetch;
  globalThis.fetch = (url, init = {}) =>
    realFetch(url, { ...init, headers: { ...init.headers, Origin: 'moz-extension://5f1d3c2a-0000-4000-8000-000000000000' } });
}

async function loadState() {
  try {
    return JSON.parse(await readFile(STATE_FILE, 'utf8'));
  } catch (error) {
    if (error.code === 'ENOENT') return { deviceId: crypto.randomUUID(), screen: null };
    throw error;
  }
}

async function saveState(state) {
  await mkdir(STATE_DIR, { recursive: true });
  await writeFile(STATE_FILE, JSON.stringify(state, null, 2));
  await chmod(STATE_FILE, 0o600);
}

function requireScreen(state) {
  if (!state.screen) throw new Error('No TV linked yet. Run: bun scripts/lounge-cli.js pair <tv code>');
  return state.screen;
}

const state = await loadState();

switch (command) {
  case 'pair': {
    state.screen = await pairWithCode(args.join(''));
    await saveState(state);
    console.log(`linked "${state.screen.name}"`);
    break;
  }
  case 'status': {
    const screen = requireScreen(state);
    console.log(`availability: ${await getAvailability(screen)}`);
    state.screen = await refreshToken(screen);
    await saveState(state);
    console.log(`after token refresh: ${await getAvailability(state.screen)}`);
    break;
  }
  case 'cast': {
    const video = parseYouTubeUrl(args[0]);
    if (!video) throw new Error(`Not a YouTube video URL: ${args[0]}`);
    if (flags.at) video.startSeconds = Number(flags.at);
    const watchSeconds = flags.watch ? Number(flags.watch === 'true' ? 15 : flags.watch) : 0;
    const result = await castVideo(requireScreen(state), video, {
      deviceId: state.deviceId,
      keepSession: watchSeconds > 0,
      onScreenUpdate: async (screen) => {
        state.screen = screen;
        await saveState(state);
      },
    });
    console.log(`sent to "${result.screen.name}" tv=${JSON.stringify(result.tv)}`);
    if (watchSeconds > 0) await watch(result.session, watchSeconds);
    break;
  }
  default:
    console.log('usage: lounge-cli.js pair <code> | status | cast <url> [--at=SECONDS] [--watch=SECONDS] [--moz-origin]');
    process.exitCode = 2;
}

async function watch(session, seconds) {
  const stop = AbortSignal.timeout(seconds * 1000);
  const interesting = new Set(['nowPlaying', 'onStateChange', 'loungeScreenDisconnected', 'onError', 'playlistModified']);
  try {
    while (!stop.aborted) {
      for await (const [id, [type, ...data]] of watchEvents(session, { signal: stop })) {
        if (interesting.has(type)) console.log(`[event ${id}] ${type} ${JSON.stringify(data)}`);
      }
    }
  } catch (error) {
    if (error.name !== 'TimeoutError' && error.name !== 'AbortError') throw error;
  }
  await disconnect(session);
}
