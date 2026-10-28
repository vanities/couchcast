export function logger(scope) {
  const tag = `[${scope}]`;
  return {
    debug: (...args) => console.debug(tag, ...args),
    info: (...args) => console.info(tag, ...args),
    warn: (...args) => console.warn(tag, ...args),
    error: (...args) => console.error(tag, ...args),
  };
}

export const since = (t0) => `${Math.round(performance.now() - t0)}ms`;

// Screen ids, lounge tokens and TV codes are bearer credentials for the TV: log only a tail.
export const mask = (value) => (value ? `…${String(value).slice(-4)}` : String(value));
