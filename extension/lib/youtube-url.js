const VIDEO_ID = /^[A-Za-z0-9_-]{11}$/;
const ID_IN_PATH = new Set(['shorts', 'live', 'embed', 'v', 'e']);

/**
 * Video, playlist and start time from any YouTube video URL (watch, youtu.be, shorts, live,
 * embed, mobile, music). Returns null for anything that isn't a single video.
 */
export function parseYouTubeUrl(input) {
  let url;
  try {
    url = new URL(input);
  } catch {
    return null;
  }
  const host = url.hostname.replace(/^(www|m|music)\./, '');
  const [, first = '', second = ''] = url.pathname.split('/');

  let videoId = null;
  if (host === 'youtu.be') videoId = first;
  else if (host === 'youtube.com' || host === 'youtube-nocookie.com') {
    if (first === 'watch') videoId = url.searchParams.get('v');
    else if (ID_IN_PATH.has(first)) videoId = second;
  }
  // "videoseries" (playlist embeds) is itself 11 URL-safe characters, so it passes the shape check.
  if (!videoId || videoId === 'videoseries' || !VIDEO_ID.test(videoId)) return null;

  const hashTime = new URLSearchParams(url.hash.slice(1)).get('t');
  return {
    videoId,
    listId: url.searchParams.get('list') || null,
    startSeconds: parseStartTime(url.searchParams.get('t') ?? url.searchParams.get('start') ?? hashTime),
  };
}

/** "90", "90s", "1m30s", "1h2m3s" to seconds. Anything else is 0. */
export function parseStartTime(value) {
  if (!value) return 0;
  if (/^\d+(\.\d+)?s?$/.test(value)) return Math.floor(parseFloat(value));
  const match = /^(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?$/.exec(value);
  if (!match) return 0;
  return Number(match[1] ?? 0) * 3600 + Number(match[2] ?? 0) * 60 + Number(match[3] ?? 0);
}
