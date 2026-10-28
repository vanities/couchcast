// storage.local layout:
//   deviceId         id this browser presents to TVs (one per install)
//   screens          [{ screenId, name, loungeToken, expiration, linkedAt, tv }]
//   defaultScreenId  which screen the toolbar button casts to

const LOUNGE_ACCESS = { origins: ['https://www.youtube.com/*'] };

export async function loadState() {
  const state = await browser.storage.local.get(['deviceId', 'screens', 'defaultScreenId']);
  if (!state.deviceId) {
    state.deviceId = crypto.randomUUID();
    await browser.storage.local.set({ deviceId: state.deviceId });
  }
  state.screens ??= [];
  return state;
}

export function defaultScreen(state) {
  return state.screens.find((s) => s.screenId === state.defaultScreenId) ?? state.screens[0] ?? null;
}

export async function saveScreen(screen) {
  const { screens = [], defaultScreenId } = await browser.storage.local.get(['screens', 'defaultScreenId']);
  const index = screens.findIndex((s) => s.screenId === screen.screenId);
  if (index >= 0) screens[index] = { ...screens[index], ...screen };
  else screens.push(screen);
  await browser.storage.local.set({ screens, defaultScreenId: defaultScreenId ?? screen.screenId });
}

export async function removeScreen(screenId) {
  const { screens = [], defaultScreenId } = await browser.storage.local.get(['screens', 'defaultScreenId']);
  const remaining = screens.filter((s) => s.screenId !== screenId);
  const nextDefault = defaultScreenId === screenId ? (remaining[0]?.screenId ?? null) : defaultScreenId;
  await browser.storage.local.set({ screens: remaining, defaultScreenId: nextDefault });
}

export async function setDefaultScreen(screenId) {
  await browser.storage.local.set({ defaultScreenId: screenId });
}

// Firefox lets people revoke host access after install, which turns every lounge call into a
// network error. Check up front so the UI can ask for it instead.
export const hasYouTubeAccess = () => browser.permissions.contains(LOUNGE_ACCESS);
export const requestYouTubeAccess = () => browser.permissions.request(LOUNGE_ACCESS);
