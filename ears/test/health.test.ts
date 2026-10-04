import assert from "node:assert/strict";
import { test } from "node:test";
import { FRAME_MS, SILENCE_FRAME, UtteranceTracker, isSilenceFrame } from "../src/health.js";

/** Feed `count` packets 20 ms apart starting at `startMs`; returns the next free time. */
function speak(t: UtteranceTracker, startMs: number, count: number, silenceTail = 0): number {
  for (let i = 0; i < count; i++) t.packet(startMs + i * FRAME_MS, i >= count - silenceTail);
  return startMs + count * FRAME_MS;
}

test("no audio means no report", () => {
  assert.equal(new UtteranceTracker().finish(), null);
});

test("a clean 1-second utterance reports 50 of 50 frames", () => {
  const t = new UtteranceTracker();
  speak(t, 1000, 50);
  assert.deepEqual(t.finish(), { framesReceived: 50, framesExpected: 50, pauses: 0, pausedMs: 0 });
});

test("dropped frames show up as a shortfall", () => {
  const t = new UtteranceTracker();
  for (let i = 0; i < 50; i++) if (i % 3 !== 2) t.packet(i * FRAME_MS, false); // first and last arrive
  const h = t.finish();
  assert.ok(h);
  assert.equal(h.framesExpected, 50);
  assert.equal(h.framesReceived, 34);
});

test("a pause after silence frames is not counted as loss (#36)", () => {
  const t = new UtteranceTracker();
  // 1 s of speech ending in 5 silence frames, a 600 ms pause, then 1 s more speech.
  const pauseStart = speak(t, 0, 50, 5);
  speak(t, pauseStart + 600, 50);
  const h = t.finish();
  assert.ok(h);
  assert.equal(h.framesReceived, 100);
  assert.equal(h.framesExpected, 100);
  assert.equal(h.pauses, 1);
  assert.equal(h.pausedMs, 600);
});

test("a gap without silence frames still counts as loss", () => {
  const t = new UtteranceTracker();
  const gapStart = speak(t, 0, 50); // no silence tail: the packets just stop
  speak(t, gapStart + 200, 50); // 200 ms = 10 missing frames
  const h = t.finish();
  assert.ok(h);
  assert.equal(h.framesReceived, 100);
  assert.equal(h.framesExpected, 110);
  assert.equal(h.pauses, 0);
});

test("loss inside speech is still caught when the utterance also has pauses", () => {
  const t = new UtteranceTracker();
  const pauseStart = speak(t, 0, 50, 5);
  // Second phrase: 50 frames' worth of time, every 5th packet lost.
  for (let i = 0; i < 50; i++) if (i % 5 !== 4 || i === 49) t.packet(pauseStart + 400 + i * FRAME_MS, false);
  const h = t.finish();
  assert.ok(h);
  assert.equal(h.framesExpected, 100);
  assert.equal(h.framesReceived, 91);
});

test("jitter right after silence frames is not a pause", () => {
  const t = new UtteranceTracker();
  const next = speak(t, 0, 5, 5);
  t.packet(next + 15, false); // 35 ms after the last silence frame: late, within PAUSE_MIN_MS
  const h = t.finish();
  assert.ok(h);
  assert.equal(h.pauses, 0);
  assert.equal(h.framesExpected, 7); // 0..115 ms spans 7 frames; nothing excluded
});

test("a single stray silence frame can't hide a gap", () => {
  const t = new UtteranceTracker();
  const gapStart = speak(t, 0, 50, 1); // only the last packet is a silence frame
  speak(t, gapStart + 200, 50);
  const h = t.finish();
  assert.ok(h);
  assert.equal(h.pauses, 0);
  assert.equal(h.framesExpected, 110);
});

test("an utterance may start with silence frames", () => {
  const t = new UtteranceTracker();
  const next = speak(t, 0, 5, 5); // all silence
  speak(t, next + 300, 20); // pause, then speech
  const h = t.finish();
  assert.ok(h);
  assert.equal(h.pauses, 1);
  assert.deepEqual([h.framesReceived, h.framesExpected], [25, 25]);
});

test("continuous silence frames never book a pause", () => {
  const t = new UtteranceTracker();
  speak(t, 0, 40, 40);
  const h = t.finish();
  assert.ok(h);
  assert.equal(h.pauses, 0);
  assert.deepEqual([h.framesReceived, h.framesExpected], [40, 40]);
});

test("trailing silence with no resumed speech is fully counted", () => {
  const t = new UtteranceTracker();
  speak(t, 0, 30, 5);
  const h = t.finish();
  assert.ok(h);
  assert.equal(h.pauses, 0);
  assert.deepEqual([h.framesReceived, h.framesExpected], [30, 30]);
});

test("known limit (#43): packets lost right after a pause are booked as the pause", () => {
  const t = new UtteranceTracker();
  const pauseStart = speak(t, 0, 50, 5);
  // Speech resumes 300 ms later, but its first 10 packets never arrive (e.g. DAVE
  // decrypt failures). Without RTP data this looks exactly like a 500 ms pause.
  speak(t, pauseStart + 300 + 10 * FRAME_MS, 40);
  const h = t.finish();
  assert.ok(h);
  assert.equal(h.framesReceived, h.framesExpected); // the loss is not visible
});

test("a jitter burst can't report more frames than expected", () => {
  const t = new UtteranceTracker();
  const next = speak(t, 0, 5, 5);
  for (let i = 0; i < 10; i++) t.packet(next + 500, false); // 10 packets delivered at once
  const h = t.finish();
  assert.ok(h);
  assert.equal(h.framesExpected, 6);
  assert.equal(h.framesReceived, 6);
});

test("finish resets for the next utterance", () => {
  const t = new UtteranceTracker();
  speak(t, 0, 10, 5);
  speak(t, 1000, 10);
  t.finish();
  t.packet(5000, false);
  assert.deepEqual(t.finish(), { framesReceived: 1, framesExpected: 1, pauses: 0, pausedMs: 0 });
});

test("isSilenceFrame matches only Discord's 3-byte silence frame", () => {
  assert.equal(isSilenceFrame(Buffer.from(SILENCE_FRAME)), true);
  assert.equal(isSilenceFrame(Buffer.from([0xf8, 0xff, 0xfe, 0x00])), false);
  assert.equal(isSilenceFrame(Buffer.from([0x78, 0x01, 0x02])), false);
});
