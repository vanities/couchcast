import { LoungeError, castVideo, getAvailability, pairWithCode, refreshToken, tokenExpiring } from './lib/lounge.js';
import { logger } from './lib/log.js';
import {
  HELPER,
  defaultScreen,
  hasHelperAccess,
  hasYouTubeAccess,
  loadMode,
  loadPauseAfterCast,
  loadUseBrowserLogin,
  loadState,
  removeScreen,
  requestHelperAccess,
  requestYouTubeAccess,
  saveMode,
  savePauseAfterCast,
  saveUseBrowserLogin,
  saveScreen,
  setDefaultScreen,
} from './lib/store.js';

const log = logger('options');
// Big Buck Bunny, from the Blender Foundation's channel.
const TEST_VIDEO = { videoId: 'aqz-KE-bpKQ', listId: null, startSeconds: 0 };
const HELPER_SETUP = { href: 'https://am2.biz/couchcast#setup', label: 'Set up the helper' };

const $ = (selector) => document.querySelector(selector);
const errorText = (error) => (error instanceof LoungeError ? error.message : `Something went wrong: ${error.message}`);

function setStatus(element, text, kind = '', link = null) {
  element.textContent = text;
  element.dataset.kind = kind;
  if (link) element.append(' ', el('a', { href: link.href, textContent: link.label, target: '_blank', rel: 'noopener' }));
}

function el(tag, props = {}, children = []) {
  const node = Object.assign(document.createElement(tag), props);
  node.append(...children);
  return node;
}

async function renderAccess() {
  $('#access').hidden = await hasYouTubeAccess();
}

$('#grant').addEventListener('click', async () => {
  const granted = await requestYouTubeAccess();
  log.info(`youtube access granted=${granted}`);
  await renderAccess();
});

$('#link-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const status = $('#link-status');
  const button = event.submitter;
  button.disabled = true;
  setStatus(status, 'Linking…');
  try {
    const screen = await pairWithCode($('#code').value);
    await saveScreen(screen);
    $('#code').value = '';
    setStatus(status, `Linked "${screen.name}". Send it a test video below to make sure it works.`, 'ok');
    await renderScreens();
  } catch (error) {
    log.error(`link failed code=${error.code ?? '-'}: ${error.message}`);
    setStatus(status, errorText(error), 'error');
  } finally {
    button.disabled = false;
  }
});

async function renderScreens() {
  const state = await loadState();
  const current = defaultScreen(state);
  $('#screens').replaceChildren(...state.screens.map((screen) => screenRow(screen, state, current)));
  $('#empty').hidden = state.screens.length > 0;
}

function screenRow(screen, state, current) {
  const pill = el('span', { className: 'pill', textContent: 'Checking…' });
  const note = el('p', { className: 'status' });
  const detail = [screen.tv?.brand, screen.tv?.model].filter(Boolean).join(' ');

  const test = el('button', { type: 'button', textContent: 'Send test video' });
  test.addEventListener('click', async () => {
    test.disabled = true;
    setStatus(note, 'Sending…');
    try {
      await castVideo(screen, TEST_VIDEO, { deviceId: state.deviceId, onScreenUpdate: saveScreen });
      setStatus(note, 'Sent. Big Buck Bunny should be starting on the TV.', 'ok');
    } catch (error) {
      log.error(`test cast failed code=${error.code ?? '-'}: ${error.message}`);
      setStatus(note, errorText(error), 'error');
    } finally {
      test.disabled = false;
      checkAvailability(screen, pill);
    }
  });

  const remove = el('button', { type: 'button', className: 'quiet', textContent: 'Remove' });
  remove.addEventListener('click', async () => {
    await removeScreen(screen.screenId);
    await renderScreens();
  });

  const actions = [test];
  if (state.screens.length > 1 && current?.screenId !== screen.screenId) {
    const makeDefault = el('button', { type: 'button', className: 'quiet', textContent: 'Make default' });
    makeDefault.addEventListener('click', async () => {
      await setDefaultScreen(screen.screenId);
      await renderScreens();
    });
    actions.push(makeDefault);
  }
  actions.push(remove);

  const title = el('div', { className: 'screen-title' }, [
    el('strong', { textContent: screen.name }),
    ...(current?.screenId === screen.screenId && state.screens.length > 1
      ? [el('span', { className: 'tag', textContent: 'default' })]
      : []),
    pill,
  ]);
  checkAvailability(screen, pill);
  return el('li', {}, [
    title,
    ...(detail ? [el('p', { className: 'muted', textContent: detail })] : []),
    el('div', { className: 'actions' }, actions),
    note,
  ]);
}

async function checkAvailability(screen, pill) {
  try {
    let current = screen;
    if (tokenExpiring(current)) {
      current = await refreshToken(current);
      await saveScreen(current);
    }
    const status = await getAvailability(current);
    const online = status === 'online';
    pill.textContent = online ? 'YouTube is open' : 'Open YouTube on the TV';
    pill.dataset.kind = online ? 'ok' : 'warn';
  } catch (error) {
    log.warn(`availability check failed: ${error.message}`);
    pill.textContent = 'Unknown';
    pill.dataset.kind = 'warn';
  }
}

$('#pause-after-cast').addEventListener('change', async (event) => {
  await savePauseAfterCast(event.target.checked);
  log.info(`pause after cast=${event.target.checked}`);
});

$('#use-browser-login').addEventListener('change', async (event) => {
  await saveUseBrowserLogin(event.target.checked);
  log.info(`use browser login on bot check=${event.target.checked}`);
});

async function renderMode() {
  const mode = await loadMode();
  $('#pause-after-cast').checked = await loadPauseAfterCast();
  $('#use-browser-login').checked = await loadUseBrowserLogin();
  $('#browser-login-setting').hidden = mode !== 'direct';
  log.info(`settings opened mode=${mode}`);
  for (const input of document.querySelectorAll('input[name=mode]')) input.checked = input.value === mode;
  $('#youtube-app-settings').hidden = mode !== 'youtube-app';
  if (mode === 'direct') await checkHelper();
  else setStatus($('#helper-status'), '');
}

for (const input of document.querySelectorAll('input[name=mode]')) {
  input.addEventListener('change', () => {
    if (input.value !== 'direct') {
      saveMode('youtube-app').then(renderMode);
      return;
    }
    // permissions.request only works inside the user's click, so nothing may be awaited first.
    requestHelperAccess().then(async (granted) => {
      log.info(`helper access granted=${granted}`);
      await saveMode(granted ? 'direct' : 'youtube-app');
      await renderMode();
    });
  });
}

async function checkHelper() {
  const status = $('#helper-status');
  if (!(await hasHelperAccess())) {
    setStatus(status, 'Couchcast needs your OK to talk to the helper app. Pick this option again and allow it.', 'error');
    return;
  }
  setStatus(status, 'Checking the helper app…');
  try {
    const reply = await browser.runtime.sendNativeMessage(HELPER, { type: 'ping' });
    log.info(`helper ping ok version=${reply?.version} device=${reply?.device ?? '-'}`);
    if (reply?.device) setStatus(status, `Helper ${reply.version} is ready and plays on ${reply.device}.`, 'ok');
    else setStatus(status, 'The helper is installed, but no Apple TV is paired yet. In a terminal, run: couchcast-helper pair', 'error');
  } catch (error) {
    log.warn(`helper ping failed: ${error.message}`);
    setStatus(status, "The Couchcast helper isn't installed on this computer yet. It takes a few commands in Terminal.", 'error', HELPER_SETUP);
  }
}

await renderAccess();
await renderMode();
await renderScreens();
