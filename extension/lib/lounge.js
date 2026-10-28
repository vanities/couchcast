// YouTube's "Link with TV code" channel (the Lounge API): the cloud relay the YouTube phone app
// uses to drive the YouTube app on a TV. It never touches the local network, which is why a
// plain extension can use it with no helper app.
// Protocol behavior follows pyytlounge (GPL-3.0): https://github.com/FabioGNR/pyytlounge

import { logger, mask, since } from './log.js';

const API = 'https://www.youtube.com/api/lounge';
const BIND = '/bc/bind';
const log = logger('lounge');

// Lounge tokens last about two weeks. Refresh an hour early so a cast never races expiry.
const REFRESH_MARGIN_MS = 60 * 60 * 1000;

export class LoungeError extends Error {
  constructor(code, message, { status, cause } = {}) {
    super(message, { cause });
    this.name = 'LoungeError';
    this.code = code;
    this.status = status;
  }
}

export function normalizeTvCode(input) {
  const digits = String(input ?? '').replace(/\D/g, '');
  if (digits.length < 8) {
    throw new LoungeError('invalid_code', 'TV codes are 12 digits. Check the code on the TV and try again.');
  }
  return digits;
}

export const tokenExpiring = (screen, now = Date.now()) =>
  !screen.loungeToken || (screen.expiration != null && screen.expiration - REFRESH_MARGIN_MS <= now);

function form(fields) {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(fields)) {
    if (value != null) params.append(key, String(value));
  }
  return params;
}

function toEpochMs(value) {
  const n = Number(value);
  if (!Number.isFinite(n) || n <= 0) return null;
  return n < 1e12 ? n * 1000 : n;
}

async function request(method, path, { query, body, signal } = {}) {
  const url = `${API}${path}${query ? `?${form(query)}` : ''}`;
  try {
    return await fetch(url, {
      method,
      body: body && form(body),
      // The lounge token is the only credential. Keep the user's Google cookies out of it.
      credentials: 'omit',
      signal,
    });
  } catch (cause) {
    if (cause?.name === 'AbortError') throw cause;
    log.warn(`${method} ${path} network error: ${cause?.message}`);
    throw new LoungeError('network', "Couldn't reach YouTube. Check your internet connection.", { cause });
  }
}

async function readJson(res, what) {
  const text = await res.text();
  if (!res.ok) {
    throw new LoungeError('http', `YouTube rejected the ${what} request (HTTP ${res.status}).`, { status: res.status });
  }
  try {
    return JSON.parse(text);
  } catch (cause) {
    throw new LoungeError('bad_response', `YouTube sent an unexpected ${what} reply.`, { status: res.status, cause });
  }
}

export async function pairWithCode(code, { signal } = {}) {
  const pairingCode = normalizeTvCode(code);
  const t0 = performance.now();
  log.info(`pair start code=${mask(pairingCode)}`);
  const res = await request('POST', '/pairing/get_screen', { body: { pairing_code: pairingCode }, signal });
  if (res.status === 404) {
    log.warn(`pair rejected code=${mask(pairingCode)} in ${since(t0)}`);
    throw new LoungeError(
      'invalid_code',
      "That code didn't work. Codes expire, so get a fresh one from the TV and try again.",
      { status: 404 },
    );
  }
  const screen = (await readJson(res, 'pairing'))?.screen;
  if (!screen?.screenId || !screen?.loungeToken) {
    throw new LoungeError('bad_response', 'YouTube sent an unexpected pairing reply.', { status: res.status });
  }
  log.info(`pair done screen=${mask(screen.screenId)} name="${screen.name}" in ${since(t0)}`);
  return {
    screenId: screen.screenId,
    name: screen.name || 'TV',
    loungeToken: screen.loungeToken,
    expiration: toEpochMs(screen.expiration),
    linkedAt: Date.now(),
  };
}

export async function refreshToken(screen, { signal } = {}) {
  const t0 = performance.now();
  const res = await request('POST', '/pairing/get_lounge_token_batch', { body: { screen_ids: screen.screenId }, signal });
  const fresh = (await readJson(res, 'token refresh'))?.screens?.[0];
  if (!fresh?.loungeToken) {
    log.warn(`refresh returned no token screen=${mask(screen.screenId)} in ${since(t0)}`);
    throw new LoungeError('unlinked', `${screen.name} is no longer linked. Link it again with a new TV code.`, {
      status: res.status,
    });
  }
  log.info(`refresh done screen=${mask(screen.screenId)} in ${since(t0)}`);
  return { ...screen, loungeToken: fresh.loungeToken, expiration: toEpochMs(fresh.expiration) };
}

/** "online" when the YouTube app on the TV is listening, otherwise whatever YouTube reports. */
export async function getAvailability(screen, { signal } = {}) {
  const t0 = performance.now();
  const res = await request('POST', '/pairing/get_screen_availability', {
    body: { lounge_token: screen.loungeToken },
    signal,
  });
  const status = (await readJson(res, 'availability'))?.screens?.[0]?.status ?? 'unknown';
  log.info(`availability screen=${mask(screen.screenId)} status=${status} in ${since(t0)}`);
  return status;
}

/**
 * Split a bind reply ("<length>\n<json array>\n", repeated) into events. The length prefix is
 * skipped rather than trusted because its unit (bytes or UTF-16 code units) is undocumented;
 * chunks are delimited by bracket matching instead. Returns the unconsumed tail so a streamed
 * reply can be fed in piece by piece.
 */
export function takeChunks(buffer) {
  const events = [];
  let pos = 0;
  for (;;) {
    const header = /^\s*\d+\s*/.exec(buffer.slice(pos));
    if (!header) break;
    const start = pos + header[0].length;
    if (start >= buffer.length) break;
    if (buffer[start] !== '[') throw new LoungeError('bad_response', 'YouTube sent a malformed session reply.');
    const end = findArrayEnd(buffer, start);
    if (end < 0) break;
    events.push(...JSON.parse(buffer.slice(start, end + 1)));
    pos = end + 1;
  }
  return { events, rest: buffer.slice(pos) };
}

function findArrayEnd(text, start) {
  let depth = 0;
  let inString = false;
  for (let i = start; i < text.length; i++) {
    const ch = text[i];
    if (inString) {
      if (ch === '\\') i++;
      else if (ch === '"') inString = false;
    } else if (ch === '"') inString = true;
    else if (ch === '[' || ch === '{') depth++;
    else if ((ch === ']' || ch === '}') && --depth === 0) return i;
  }
  return -1;
}

export function sessionFromEvents(events) {
  const session = { sid: null, gsessionid: null, aid: null, tv: null };
  for (const [id, [type, ...args]] of events) {
    session.aid = id;
    if (type === 'c') session.sid = args[0];
    else if (type === 'S') session.gsessionid = args[0];
    else if (type === 'loungeStatus') session.tv = tvFromLoungeStatus(args[0]);
  }
  return session;
}

export function tvFromLoungeStatus(status) {
  let devices;
  try {
    devices = JSON.parse(status?.devices ?? '[]');
  } catch (error) {
    log.warn(`loungeStatus devices unparseable: ${error.message}`);
    return null;
  }
  const tv = devices.find((device) => device.type === 'LOUNGE_SCREEN');
  if (!tv) return null;
  let info = {};
  try {
    info = JSON.parse(tv.deviceInfo || '{}');
  } catch (error) {
    log.debug(`deviceInfo unparseable: ${error.message}`);
  }
  return { name: tv.name ?? null, brand: info.brand ?? null, model: info.model ?? null };
}

function sessionQuery(session, extra) {
  return {
    name: session.deviceName,
    loungeIdToken: session.loungeToken,
    SID: session.sid,
    AID: session.aid,
    gsessionid: session.gsessionid,
    device: 'REMOTE_CONTROL',
    app: 'youtube-desktop',
    VER: 8,
    v: 2,
    ...extra,
  };
}

export async function connect(screen, { deviceId, deviceName = 'Firefox', signal } = {}) {
  const t0 = performance.now();
  const res = await request('POST', BIND, {
    query: { RID: 1, VER: 8, CVER: 1, auth_failure_option: 'send_error' },
    body: {
      app: 'web',
      'mdx-version': 3,
      name: deviceName,
      id: deviceId,
      device: 'REMOTE_CONTROL',
      capabilities: 'que,dsdtr,atp,vsp',
      magnaKey: 'cloudPairedDevice',
      ui: 'false',
      deviceContext: 'user_agent=dunno&window_width_points=&window_height_points=&os_name=android&ms=',
      theme: 'cl',
      loungeIdToken: screen.loungeToken,
    },
    signal,
  });
  const text = await res.text();
  if (res.status === 401) throw new LoungeError('token_expired', 'The TV link token expired.', { status: 401 });
  if (!res.ok) {
    log.warn(`bind failed status=${res.status} body=${text.slice(0, 200)}`);
    throw new LoungeError('bind_failed', `Couldn't open a session with ${screen.name} (HTTP ${res.status}).`, {
      status: res.status,
    });
  }
  const { events } = takeChunks(text);
  const session = { ...sessionFromEvents(events), deviceName, loungeToken: screen.loungeToken, rid: 1 };
  if (!session.sid || !session.gsessionid) {
    throw new LoungeError('bad_response', 'YouTube did not open a session with the TV.', { status: res.status });
  }
  log.info(`bind done events=${events.length} tv=${session.tv ? `"${session.tv.name}"` : 'absent'} in ${since(t0)}`);
  return session;
}

async function sendCommand(session, name, params = {}, { signal } = {}) {
  const t0 = performance.now();
  session.rid += 1;
  const body = { count: 1, ofs: session.rid - 1, req0__sc: name };
  for (const [key, value] of Object.entries(params)) body[`req0_${key}`] = value;
  const res = await request('POST', BIND, { query: sessionQuery(session, { RID: session.rid }), body, signal });
  const text = await res.text();
  if ((res.status === 400 && /Unknown SID/i.test(text)) || res.status === 410) {
    throw new LoungeError('session_lost', 'The TV session ended.', { status: res.status });
  }
  if (res.status === 401) throw new LoungeError('token_expired', 'The TV link token expired.', { status: 401 });
  if (!res.ok) {
    log.warn(`command ${name} failed status=${res.status} body=${text.slice(0, 200)}`);
    throw new LoungeError('command_failed', `The TV rejected "${name}" (HTTP ${res.status}).`, { status: res.status });
  }
  log.info(`command ${name} ok rid=${session.rid} in ${since(t0)}`);
}

export async function disconnect(session, { signal } = {}) {
  session.rid += 1;
  const res = await request('POST', BIND, {
    query: sessionQuery(session, { RID: session.rid, CVER: 1, auth_failure_option: 'send_error' }),
    body: { ui: '', TYPE: 'terminate', clientDisconnectReason: 'MDX_SESSION_DISCONNECT_REASON_DISCONNECTED_BY_USER' },
    signal,
  });
  await res.text();
  log.info(`disconnect status=${res.status}`);
}

/** Stream TV events (nowPlaying, onStateChange, ...) for an open session until it closes. */
export async function* watchEvents(session, { signal } = {}) {
  const res = await request('GET', BIND, { query: sessionQuery(session, { RID: 'rpc', CI: 0, TYPE: 'xmlhttp' }), signal });
  if (!res.ok) throw new LoungeError('session_lost', `The TV session ended (HTTP ${res.status}).`, { status: res.status });
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  try {
    for (;;) {
      const { value, done } = await reader.read();
      if (done) return;
      buffer += decoder.decode(value, { stream: true });
      const { events, rest } = takeChunks(buffer);
      buffer = rest;
      for (const event of events) {
        session.aid = event[0];
        yield event;
      }
    }
  } finally {
    await reader.cancel().catch((error) => log.debug(`event stream cancel: ${error.message}`));
  }
}

/**
 * Play a video on a linked TV. startSeconds null means "live edge / TV default". Calls
 * onScreenUpdate with the refreshed screen whenever the token is renewed, so callers can persist it.
 */
export async function castVideo(
  screen,
  video,
  { deviceId, deviceName, signal, onScreenUpdate, keepSession = false } = {},
) {
  const t0 = performance.now();
  log.info(
    `cast start tv="${screen.name}" video=${video.videoId} list=${video.listId ?? '-'} at=${video.startSeconds ?? 'default'}`,
  );
  let current = screen;
  const refresh = async () => {
    current = await refreshToken(current, { signal });
    await onScreenUpdate?.(current);
  };
  if (tokenExpiring(current)) await refresh();

  let status = await getAvailability(current, { signal });
  if (status === 'unknown') {
    // An unrecognized token gets an empty 200 reply, not a 401.
    await refresh();
    status = await getAvailability(current, { signal });
  }
  if (status !== 'online') {
    throw new LoungeError('tv_offline', `${current.name} isn't listening. Open the YouTube app on the TV and try again.`);
  }

  let session;
  try {
    session = await connect(current, { deviceId, deviceName, signal });
  } catch (error) {
    if (error.code !== 'token_expired') throw error;
    await refresh();
    session = await connect(current, { deviceId, deviceName, signal });
  }

  await sendCommand(
    session,
    'setPlaylist',
    {
      videoId: video.videoId,
      listId: video.listId,
      currentTime: video.startSeconds == null ? null : Math.max(0, Math.floor(video.startSeconds)),
      currentIndex: -1,
      audioOnly: false,
    },
    { signal },
  );
  log.info(`cast sent tv="${current.name}" video=${video.videoId} in ${since(t0)}`);

  if (!keepSession) {
    await disconnect(session, { signal }).catch((error) => log.warn(`disconnect failed: ${error.message}`));
  }
  return { screen: current, tv: session.tv, session };
}
