import { describe, expect, test } from 'bun:test';
import { parseStartTime, parseYouTubeUrl } from '../extension/lib/youtube-url.js';

const ID = 'aqz-KE-bpKQ';
const video = (extra = {}) => ({ videoId: ID, listId: null, startSeconds: 0, ...extra });

describe('parseYouTubeUrl', () => {
  test.each([
    [`https://www.youtube.com/watch?v=${ID}`, video()],
    [`https://youtube.com/watch?v=${ID}&t=42`, video({ startSeconds: 42 })],
    [`https://youtu.be/${ID}?t=42`, video({ startSeconds: 42 })],
    [`https://m.youtube.com/watch?v=${ID}&t=1m30s`, video({ startSeconds: 90 })],
    [`https://music.youtube.com/watch?v=${ID}&list=PLabc`, video({ listId: 'PLabc' })],
    [`https://www.youtube.com/watch?v=${ID}&list=PLabc&index=3`, video({ listId: 'PLabc' })],
    [`https://www.youtube.com/shorts/${ID}`, video()],
    [`https://www.youtube.com/live/${ID}?feature=share`, video()],
    [`https://www.youtube.com/embed/${ID}?start=30`, video({ startSeconds: 30 })],
    [`https://www.youtube-nocookie.com/embed/${ID}`, video()],
    [`https://www.youtube.com/watch?v=${ID}#t=1h2m3s`, video({ startSeconds: 3723 })],
  ])('%s', (url, expected) => {
    expect(parseYouTubeUrl(url)).toEqual(expected);
  });

  test.each([
    'https://www.youtube.com/',
    'https://www.youtube.com/@blender',
    'https://www.youtube.com/playlist?list=PLabc',
    'https://www.youtube.com/embed/videoseries?list=PLabc',
    'https://www.youtube.com/watch?v=tooshort',
    'https://vimeo.com/76979871',
    `https://example.com/watch?v=${ID}`,
    `https://youtube.com.example.com/watch?v=${ID}`,
    'about:blank',
    'not a url',
  ])('rejects %s', (url) => {
    expect(parseYouTubeUrl(url)).toBeNull();
  });
});

describe('parseStartTime', () => {
  test.each([
    [null, 0],
    ['', 0],
    ['75', 75],
    ['75s', 75],
    ['12.9', 12],
    ['2m', 120],
    ['1h', 3600],
    ['1h2m3s', 3723],
    ['soon', 0],
  ])('%p -> %p', (input, seconds) => {
    expect(parseStartTime(input)).toBe(seconds);
  });
});
