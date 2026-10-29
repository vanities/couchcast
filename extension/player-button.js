// Adds a Cast button to YouTube's player controls, in the slot YouTube keeps for its own
// "Play on TV" button (always hidden in Firefox, which has no Cast support).

const BUTTON_CLASS = 'couchcast-button';
// Material Design "cast" icon (Apache-2.0).
const CAST_ICON =
  'M21 3H3c-1.1 0-2 .9-2 2v3h2V5h18v14h-7v2h7c1.1 0 2-.9 2-2V5c0-1.1-.9-2-2-2zM1 18v3h3c0-1.66-1.34-3-3-3zm0-4v2c2.76 0 5 2.24 5 5h2c0-3.87-3.13-7-7-7zm0-4v2c4.97 0 9 4.03 9 9h2c0-6.08-4.93-11-11-11z';

let label = 'Cast to TV';
let tipTimer = null;

function castIcon() {
  const ns = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(ns, 'svg');
  svg.setAttribute('viewBox', '0 0 24 24');
  svg.setAttribute('width', '24');
  svg.setAttribute('height', '24');
  svg.setAttribute('aria-hidden', 'true');
  const path = document.createElementNS(ns, 'path');
  path.setAttribute('d', CAST_ICON);
  path.setAttribute('fill', 'white');
  svg.append(path);
  return svg;
}

function createButton() {
  const button = document.createElement('button');
  button.className = `ytp-button ${BUTTON_CLASS}`;
  button.setAttribute('aria-label', label);
  button.append(castIcon());
  button.addEventListener('click', onClick);
  button.addEventListener('mouseenter', () => {
    showTip(button, label);
    refreshLabel(button);
  });
  button.addEventListener('mouseleave', () => hideTip(button));
  return button;
}

function inject() {
  for (const controls of document.querySelectorAll('.html5-video-player .ytp-right-controls')) {
    if (controls.querySelector(`.${BUTTON_CLASS}`)) continue;
    // Home-page hover previews reuse the player but aren't something you'd cast.
    if (controls.closest('ytd-video-preview, #inline-preview-player')) continue;
    const button = createButton();
    const slot = controls.querySelector('.ytp-remote-button') ?? controls.querySelector('.ytp-fullscreen-button');
    if (slot) slot.before(button);
    else controls.append(button);
    console.debug('[couchcast] cast button added', slot ? `before .${slot.classList[0]}` : 'at end');
  }
}

async function refreshLabel(button) {
  try {
    const reply = await browser.runtime.sendMessage({ type: 'player-label' });
    if (!reply?.label || reply.label === label) return;
    label = reply.label;
    button.setAttribute('aria-label', label);
    if (button.matches(':hover')) showTip(button, label);
  } catch (error) {
    console.warn('[couchcast] label lookup failed:', error.message);
  }
}

async function onClick(event) {
  event.preventDefault();
  event.stopPropagation();
  const button = event.currentTarget;
  if (button.classList.contains('busy')) return;
  button.classList.add('busy');
  showTip(button, 'Sending to TV…');
  const t0 = performance.now();
  console.info('[couchcast] cast clicked', location.href);
  try {
    const reply = await browser.runtime.sendMessage({ type: 'cast-from-player', url: location.href });
    console.info('[couchcast] cast reply', reply, `${Math.round(performance.now() - t0)}ms`);
    if (reply?.ok) showTip(button, `Playing on ${reply.tvName}`, 'ok', 3500);
    else showTip(button, reply?.message ?? 'Cast failed.', 'error', 6000);
  } catch (error) {
    console.error('[couchcast] cast message failed:', error);
    showTip(button, `Couchcast isn't responding: ${error.message}`, 'error', 6000);
  } finally {
    button.classList.remove('busy');
  }
}

function showTip(button, text, kind = '', hideAfterMs = 0) {
  const player = button.closest('.html5-video-player');
  if (!player) return;
  let tip = player.querySelector('.couchcast-tip');
  if (!tip) {
    tip = document.createElement('div');
    tip.className = 'couchcast-tip';
    tip.setAttribute('role', 'status');
    player.append(tip);
  }
  const b = button.getBoundingClientRect();
  const p = player.getBoundingClientRect();
  tip.textContent = text;
  tip.dataset.kind = kind;
  // Same spot as YouTube's own tooltips: centered on the button, 22px above it.
  tip.style.left = `${b.left - p.left + b.width / 2}px`;
  tip.style.top = `${b.top - p.top - 22}px`;
  tip.classList.add('visible');
  clearTimeout(tipTimer);
  if (hideAfterMs) tipTimer = setTimeout(() => tip.classList.remove('visible'), hideAfterMs);
}

function hideTip(button) {
  const tip = button.closest('.html5-video-player')?.querySelector('.couchcast-tip');
  // Leave result messages up for their full time; only plain hover labels follow the mouse.
  if (tip && !tip.dataset.kind && !button.classList.contains('busy')) tip.classList.remove('visible');
}

// Coming back to a tab whose video is playing on the TV (no-ads mode): jump the paused local
// player to where the TV is, so pressing play here continues from the right spot.
let lastCatchUp = 0;
async function catchUpWithTv() {
  if (window.top !== window || document.visibilityState !== 'visible') return;
  // visibilitychange and focus usually fire together.
  if (performance.now() - lastCatchUp < 2000) return;
  lastCatchUp = performance.now();
  const videoId = new URL(location.href).searchParams.get('v');
  const video = document.querySelector('#movie_player video');
  if (!videoId || !video || !video.paused) return;
  try {
    const status = await browser.runtime.sendMessage({ type: 'tv-position', videoId });
    if (!status?.active || status.position == null) return;
    const behind = status.position - video.currentTime;
    console.info('[couchcast] tv position', status.position.toFixed(1), 'local', video.currentTime.toFixed(1));
    if (Math.abs(behind) < 3) return;
    video.currentTime = status.position;
    const button = document.querySelector(`#movie_player .${BUTTON_CLASS}`);
    if (button) showTip(button, `Caught up with ${status.tvName}`, 'ok', 3000);
  } catch (error) {
    console.warn('[couchcast] catch-up failed:', error.message);
  }
}
document.addEventListener('visibilitychange', catchUpWithTv);
window.addEventListener('focus', catchUpWithTv);

let scheduled = false;
new MutationObserver(() => {
  if (scheduled) return;
  scheduled = true;
  requestAnimationFrame(() => {
    scheduled = false;
    inject();
  });
}).observe(document.documentElement, { childList: true, subtree: true });
inject();
