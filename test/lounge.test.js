import { afterEach, describe, expect, test } from 'bun:test';
import {
  LoungeError,
  castVideo,
  normalizeTvCode,
  pairWithCode,
  sessionFromEvents,
  takeChunks,
  tokenExpiring,
} from '../extension/lib/lounge.js';

const chunk = (events) => {
  const json = JSON.stringify(events);
  return `${json.length + 1}\n${json}\n`;
};

const tvStatus = (name) => [
  'loungeStatus',
  {
    devices: JSON.stringify([
      { type: 'REMOTE_CONTROL', name: 'Phone' },
      { type: 'LOUNGE_SCREEN', name, deviceInfo: JSON.stringify({ brand: 'Apple', model: 'AppleTV11,1' }) },
    ]),
  },
];

const BIND_REPLY = chunk([[0, ['c', 'SID-1', '', 8]], [1, ['S', 'GS-1']]]) + chunk([[2, tvStatus('Bedroom')]]);

describe('takeChunks', () => {
  test('turns a bind reply into session ids and the TV identity', () => {
    const { events, rest } = takeChunks(BIND_REPLY);
    expect(rest.trim()).toBe('');
    expect(sessionFromEvents(events)).toEqual({
      sid: 'SID-1',
      gsessionid: 'GS-1',
      aid: 2,
      tv: { name: 'Bedroom', brand: 'Apple', model: 'AppleTV11,1' },
    });
  });

  test('holds back a partial chunk until the rest of the stream arrives', () => {
    const cut = BIND_REPLY.length - 10;
    const first = takeChunks(BIND_REPLY.slice(0, cut));
    expect(first.events).toHaveLength(2);
    const second = takeChunks(first.rest + BIND_REPLY.slice(cut));
    expect(second.events).toHaveLength(1);
    expect(second.rest.trim()).toBe('');
  });

  test('is not fooled by brackets, quotes or multibyte text inside strings', () => {
    const tricky = chunk([[5, ['nowPlaying', { title: 'a ] b [ "c" \\ 📺 }' }]]]);
    expect(takeChunks(tricky).events).toEqual([[5, ['nowPlaying', { title: 'a ] b [ "c" \\ 📺 }' }]]]);
  });

  test('rejects a reply that is not chunked JSON', () => {
    expect(() => takeChunks('12\n<html>')).toThrow(LoungeError);
  });
});

describe('normalizeTvCode', () => {
  test('strips spaces and dashes', () => {
    expect(normalizeTvCode(' 123 456-789 012 ')).toBe('123456789012');
  });
  test('rejects short input', () => {
    expect(() => normalizeTvCode('12 34')).toThrow(/12 digits/);
  });
});

describe('tokenExpiring', () => {
  const now = 1_800_000_000_000;
  test('missing token', () => expect(tokenExpiring({}, now)).toBe(true));
  test('unknown expiry is trusted until YouTube says otherwise', () =>
    expect(tokenExpiring({ loungeToken: 't', expiration: null }, now)).toBe(false));
  test('inside the refresh margin', () =>
    expect(tokenExpiring({ loungeToken: 't', expiration: now + 60_000 }, now)).toBe(true));
  test('fresh', () => expect(tokenExpiring({ loungeToken: 't', expiration: now + 86_400_000 }, now)).toBe(false));
});

// A fake www.youtube.com/api/lounge that records every request.
function fakeYouTube({ availability = ['online'], pairStatus = 200, bindStatus = 200 } = {}) {
  const calls = [];
  const statuses = [...availability];
  const reply = (body, status = 200) => new Response(typeof body === 'string' ? body : JSON.stringify(body), { status });
  globalThis.fetch = async (input, init = {}) => {
    const url = new URL(input);
    const body = Object.fromEntries(new URLSearchParams(init.body ?? ''));
    const path = url.pathname.replace('/api/lounge', '');
    calls.push({ method: init.method, path, query: Object.fromEntries(url.searchParams), body, credentials: init.credentials });
    if (path === '/pairing/get_screen') {
      if (pairStatus !== 200) return reply('<html>Not Found</html>', pairStatus);
      return reply({ screen: { screenId: 'screen-1', name: 'YouTube on TV', loungeToken: 'tok-1', expiration: 1_900_000_000_000 } });
    }
    if (path === '/pairing/get_lounge_token_batch') {
      return reply({ screens: [{ screenId: body.screen_ids, loungeToken: 'tok-fresh', expiration: Date.now() + 14 * 86_400_000 }] });
    }
    if (path === '/pairing/get_screen_availability') {
      const status = statuses.length > 1 ? statuses.shift() : statuses[0];
      return reply(status === 'unknown' ? {} : { screens: [{ status }] });
    }
    if (path === '/bc/bind') {
      if (body.req0__sc || body.TYPE === 'terminate') return reply('[1,0,0]');
      return bindStatus === 200 ? reply(BIND_REPLY) : reply('Unauthorized', bindStatus);
    }
    return reply('unexpected', 500);
  };
  return calls;
}

const realFetch = globalThis.fetch;
afterEach(() => {
  globalThis.fetch = realFetch;
});

const SCREEN = { screenId: 'screen-1', name: 'Bedroom', loungeToken: 'tok-1', expiration: Date.now() + 7 * 86_400_000 };
const OPTS = { deviceId: 'device-1', deviceName: 'Firefox' };

describe('pairWithCode', () => {
  test('returns a storable screen', async () => {
    const calls = fakeYouTube();
    const screen = await pairWithCode('123 456 789 012');
    expect(screen).toMatchObject({ screenId: 'screen-1', name: 'YouTube on TV', loungeToken: 'tok-1', expiration: 1_900_000_000_000 });
    expect(calls[0].body).toEqual({ pairing_code: '123456789012' });
    expect(calls[0].credentials).toBe('omit');
  });

  test('a 404 means the code was wrong or expired', async () => {
    fakeYouTube({ pairStatus: 404 });
    await expect(pairWithCode('000000000000')).rejects.toMatchObject({ code: 'invalid_code' });
  });
});

describe('castVideo', () => {
  test('checks the TV, binds, plays at the given time, then hangs up', async () => {
    const calls = fakeYouTube();
    const result = await castVideo(SCREEN, { videoId: 'aqz-KE-bpKQ', listId: 'PLabc', startSeconds: 61.7 }, OPTS);

    expect(calls.map((c) => c.path)).toEqual(['/pairing/get_screen_availability', '/bc/bind', '/bc/bind', '/bc/bind']);
    const [, bind, play, hangUp] = calls;
    expect(bind.body).toMatchObject({ device: 'REMOTE_CONTROL', id: 'device-1', name: 'Firefox', loungeIdToken: 'tok-1' });
    expect(play.query).toMatchObject({ SID: 'SID-1', gsessionid: 'GS-1', AID: '2', RID: '2' });
    expect(play.body).toEqual({
      count: '1',
      ofs: '1',
      req0__sc: 'setPlaylist',
      req0_videoId: 'aqz-KE-bpKQ',
      req0_listId: 'PLabc',
      req0_currentTime: '61',
      req0_currentIndex: '-1',
      req0_audioOnly: 'false',
    });
    expect(hangUp.body.TYPE).toBe('terminate');
    expect(hangUp.query.RID).toBe('3');
    expect(calls.every((c) => c.credentials === 'omit')).toBe(true);
    expect(result.tv).toEqual({ name: 'Bedroom', brand: 'Apple', model: 'AppleTV11,1' });
  });

  test('omits currentTime and listId when there is none (live streams, plain videos)', async () => {
    const calls = fakeYouTube();
    await castVideo(SCREEN, { videoId: 'aqz-KE-bpKQ', listId: null, startSeconds: null }, OPTS);
    expect(calls[2].body).not.toHaveProperty('req0_currentTime');
    expect(calls[2].body).not.toHaveProperty('req0_listId');
  });

  test('refreshes an expiring token first and reports it for saving', async () => {
    const calls = fakeYouTube();
    const saved = [];
    const result = await castVideo(
      { ...SCREEN, expiration: Date.now() + 1000 },
      { videoId: 'aqz-KE-bpKQ', startSeconds: 0 },
      { ...OPTS, onScreenUpdate: (s) => saved.push(s) },
    );
    expect(calls[0].path).toBe('/pairing/get_lounge_token_batch');
    expect(calls[0].body).toEqual({ screen_ids: 'screen-1' });
    expect(saved).toHaveLength(1);
    expect(result.screen.loungeToken).toBe('tok-fresh');
    expect(calls[2].body.loungeIdToken).toBe('tok-fresh');
  });

  test('an empty availability reply triggers one token refresh', async () => {
    const calls = fakeYouTube({ availability: ['unknown', 'online'] });
    await castVideo(SCREEN, { videoId: 'aqz-KE-bpKQ', startSeconds: 0 }, OPTS);
    expect(calls.map((c) => c.path).slice(0, 3)).toEqual([
      '/pairing/get_screen_availability',
      '/pairing/get_lounge_token_batch',
      '/pairing/get_screen_availability',
    ]);
  });

  test('a TV with YouTube closed fails fast with a clear message', async () => {
    const calls = fakeYouTube({ availability: ['offline'] });
    await expect(castVideo(SCREEN, { videoId: 'aqz-KE-bpKQ', startSeconds: 0 }, OPTS)).rejects.toMatchObject({
      code: 'tv_offline',
      message: expect.stringContaining('Open the YouTube app'),
    });
    expect(calls.some((c) => c.path === '/bc/bind')).toBe(false);
  });
});
