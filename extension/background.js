import { LoungeError, castVideo } from './lib/lounge.js';
import { logger, since } from './lib/log.js';
import {
  HELPER,
  defaultScreen,
  hasHelperAccess,
  hasYouTubeAccess,
  loadMode,
  loadPauseAfterCast,
  loadState,
  loadUseBrowserLogin,
  saveScreen,
} from './lib/store.js';
import { parseYouTubeUrl } from './lib/youtube-url.js';

const log = logger('cast');

const VIDEO_LINKS = [
  '*://*.youtube.com/watch*',
  '*://*.youtube.com/shorts/*',
  '*://*.youtube.com/live/*',
  '*://*.youtube.com/embed/*',
  '*://*.youtube-nocookie.com/embed/*',
  '*://youtu.be/*',
];
const YOUTUBE_DOCUMENTS = ['*://*.youtube.com/*', '*://*.youtube-nocookie.com/*'];

async function createMenus() {
  // Firefox for Android has no context menus.
  if (!browser.contextMenus) return;
  await browser.contextMenus.removeAll();
  browser.contextMenus.create({
    id: 'cast-link',
    title: 'Cast to TV',
    contexts: ['link'],
    targetUrlPatterns: VIDEO_LINKS,
  });
  browser.contextMenus.create({
    id: 'cast-page',
    title: 'Cast this video to TV',
    contexts: ['page', 'video', 'frame'],
    documentUrlPatterns: YOUTUBE_DOCUMENTS,
  });
}

browser.runtime.onInstalled.addListener(async ({ reason }) => {
  log.info(`installed reason=${reason} version=${browser.runtime.getManifest().version}`);
  await createMenus();
  if (reason === 'install') await browser.runtime.openOptionsPage();
});
browser.runtime.onStartup.addListener(createMenus);

browser.action.onClicked.addListener((tab) => castFromTab(tab));

browser.contextMenus?.onClicked.addListener((info, tab) => {
  if (info.menuItemId === 'cast-link') return castVideoTo(parseYouTubeUrl(info.linkUrl), tab, { source: 'link' });
  const embedded = info.frameId ? parseYouTubeUrl(info.frameUrl) : null;
  if (embedded) return castFromTab(tab, { frameId: info.frameId, fromUrl: embedded });
  return castFromTab(tab);
});

browser.runtime.onMessage.addListener((message, sender) => {
  log.debug(`message type=${message?.type} tab=${sender.tab?.id} frame=${sender.frameId}`);
  if (message?.type === 'cast-from-player') {
    // Not sender.url: Firefox keeps the URL from the content script's first message, and YouTube
    // switches videos without loading a new page, so that can be a video from minutes ago.
    log.debug(`cast-from-player page=${message.url} sender.url=${sender.url}`);
    // The in-player button shows the outcome itself, so skip the system notification.
    return castFromTab(sender.tab, { frameId: sender.frameId, fromUrl: parseYouTubeUrl(message.url), notify: false });
  }
  if (message?.type === 'player-label') return playerLabel();
  if (message?.type === 'tv-position') return tvPosition(message.videoId);
  return undefined;
});

/** Where the TV is in this video, so a tab you come back to can catch up. No-ads mode only:
 * the helper's player records the TV's position from its live status updates. */
async function tvPosition(videoId) {
  if ((await loadMode()) !== 'direct' || !(await hasHelperAccess())) return { active: false };
  const t0 = performance.now();
  try {
    const status = await browser.runtime.sendNativeMessage(HELPER, { type: 'status' });
    const match = status?.active && status.videoId === videoId;
    log.info(
      `tv position video=${videoId} active=${status?.active} tvVideo=${status?.videoId ?? '-'} state=${status?.state ?? '-'} at=${status?.position?.toFixed?.(1) ?? '-'} in ${since(t0)}`,
    );
    return match ? status : { active: false };
  } catch (error) {
    log.warn(`tv position lookup failed in ${since(t0)}: ${error.message}`);
    return { active: false };
  }
}

async function playerLabel() {
  if ((await loadMode()) === 'direct') return { label: 'Play on TV, no ads' };
  const screen = defaultScreen(await loadState());
  return { label: screen ? `Play on ${screen.name}` : 'Cast to TV' };
}

async function castFromTab(tab, { frameId = 0, fromUrl = parseYouTubeUrl(tab.url), notify = true } = {}) {
  log.info(`cast requested tab=${tab?.id} frame=${frameId} url=${tab?.url} urlVideo=${fromUrl?.videoId ?? '-'}`);
  const player = await readPlayer(tab.id, frameId);
  const videoId = fromUrl?.videoId ?? player?.videoId;
  if (!videoId) return fail(tab, 'Open a YouTube video first, then cast.', notify);
  if (player?.videoId && player.videoId !== videoId) {
    log.warn(`the page is on video=${videoId} but its player has video=${player.videoId}; casting the page's`);
  }

  // Only trust the player's position when it is showing the same video as the URL (a Shorts
  // page or the miniplayer can hold a stale or different one).
  const live = player?.videoId === videoId ? player : null;
  const video = {
    videoId,
    listId: fromUrl?.listId ?? live?.listId ?? null,
    startSeconds: live?.isLive ? null : (live?.time ?? fromUrl?.startSeconds ?? 0),
    rate: live?.rate ?? 1,
  };
  log.info(
    `cast video=${videoId} list=${video.listId ?? '-'} at=${video.startSeconds ?? 'live'} rate=${video.rate}x from=${live ? 'player' : 'url'}`,
  );
  const result = await castVideoTo(video, tab, { source: frameId ? 'embed' : 'tab', notify });
  if (result.ok && (await loadPauseAfterCast())) await pausePage(tab.id, frameId);
  return result;
}

async function castVideoTo(video, tab, { source, notify = true }) {
  if (!video) return fail(tab, "That link isn't a YouTube video.", notify);
  const mode = await loadMode();
  const t0 = performance.now();
  await setBadge(tab, '…', '#6b7280');
  try {
    const result = mode === 'direct' ? await castDirect(video, tab, notify) : await castViaYouTubeApp(video, tab, notify);
    if (!result.ok) return result;
    log.info(`cast ok mode=${mode} source=${source} video=${video.videoId} tv="${result.tvName}" in ${since(t0)}`);
    await setBadge(tab, '✓', '#16a34a', `Playing on ${result.tvName}`);
    return result;
  } catch (error) {
    log.error(
      `cast failed mode=${mode} source=${source} video=${video.videoId} code=${error.code ?? '-'} status=${error.status ?? '-'} in ${since(t0)}: ${error.message}`,
    );
    return fail(tab, error instanceof LoungeError ? error.message : `Something went wrong: ${error.message}`, notify);
  }
}

async function castViaYouTubeApp(video, tab, notify) {
  const state = await loadState();
  const screen = defaultScreen(state);
  if (!screen) {
    await browser.runtime.openOptionsPage();
    return fail(tab, 'Link your TV first: in the YouTube app on the TV, open Settings, then Link with TV code.', notify);
  }
  if (!(await hasYouTubeAccess())) {
    await browser.runtime.openOptionsPage();
    return fail(tab, 'Couchcast needs permission to reach youtube.com. Allow it on the settings page.', notify);
  }
  const result = await castVideo(screen, video, { deviceId: state.deviceId, onScreenUpdate: saveScreen });
  if (result.tv) await saveScreen({ screenId: screen.screenId, tv: result.tv });
  return { ok: true, tvName: screen.name };
}

async function castDirect(video, tab, notify) {
  if (!(await hasHelperAccess())) {
    await browser.runtime.openOptionsPage();
    return fail(tab, 'No-ads mode needs permission to talk to the Couchcast helper. Allow it on the settings page.', notify);
  }
  const t0 = performance.now();
  let reply;
  try {
    reply = await browser.runtime.sendNativeMessage(HELPER, {
      type: 'cast',
      videoId: video.videoId,
      startSeconds: video.startSeconds,
      rate: video.rate,
      useBrowserLogin: await loadUseBrowserLogin(),
    });
  } catch (error) {
    log.error(`helper unreachable in ${since(t0)}: ${error.message}`);
    return fail(tab, "The Couchcast helper app isn't installed or didn't start. See the settings page.", notify);
  }
  log.info(`helper replied ok=${reply?.ok} state=${reply?.state ?? '-'} in ${since(t0)}`);
  if (!reply?.ok) return fail(tab, reply?.error ?? 'The Couchcast helper gave no answer.', notify);
  return { ok: true, tvName: reply.tvName };
}

async function readPlayer(tabId, frameId) {
  const t0 = performance.now();
  try {
    const [injection] = await browser.scripting.executeScript({
      target: { tabId, frameIds: [frameId] },
      // YouTube's player API (getVideoData, getCurrentTime) is only reachable from the page's world.
      world: 'MAIN',
      func: () => {
        const player = document.getElementById('movie_player');
        const data = player?.getVideoData?.();
        const element = document.querySelector('video');
        return {
          videoId: data?.video_id || null,
          listId: data?.list || null,
          isLive: Boolean(data?.isLive),
          time: player?.getCurrentTime?.() ?? element?.currentTime ?? null,
          rate: player?.getPlaybackRate?.() ?? element?.playbackRate ?? 1,
        };
      },
    });
    log.debug(`player tab=${tabId} frame=${frameId} ${JSON.stringify(injection?.result)} in ${since(t0)}`);
    return injection?.result ?? null;
  } catch (error) {
    log.warn(`player read failed tab=${tabId} frame=${frameId}: ${error.message}`);
    return null;
  }
}

async function pausePage(tabId, frameId) {
  try {
    await browser.scripting.executeScript({
      target: { tabId, frameIds: [frameId] },
      func: () => document.querySelectorAll('video').forEach((video) => video.pause()),
    });
    log.debug(`paused the page's video tab=${tabId} frame=${frameId}`);
  } catch (error) {
    log.warn(`pause failed tab=${tabId}: ${error.message}`);
  }
}

async function setBadge(tab, text, color, title) {
  const tabId = tab?.id;
  await browser.action.setBadgeBackgroundColor({ color, tabId });
  await browser.action.setBadgeText({ text, tabId });
  if (title) await browser.action.setTitle({ title, tabId });
  if (text === '…') return;
  setTimeout(() => {
    browser.action.setBadgeText({ text: '', tabId });
    browser.action.setTitle({ title: null, tabId });
  }, 6000);
}

async function fail(tab, message, notify = true) {
  await setBadge(tab, '!', '#dc2626', message);
  if (notify) await browser.notifications.create({ type: 'basic', title: "Couldn't cast", message });
  return { ok: false, message };
}
