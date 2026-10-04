import assert from "node:assert/strict";
import { test } from "node:test";
import { UtteranceTracker } from "../src/health.js";

test("no audio means no report", () => {
  assert.equal(new UtteranceTracker().finish(), null);
});

test("a clean 1-second utterance reports 50 of 50 frames", () => {
  const t = new UtteranceTracker();
  for (let i = 0; i < 50; i++) t.frame(1000 + i * 20);
  assert.deepEqual(t.finish(), { framesReceived: 50, framesExpected: 50 });
});

test("dropped frames show up as a shortfall", () => {
  const t = new UtteranceTracker();
  for (let i = 0; i < 50; i++) if (i % 3 !== 2) t.frame(i * 20); // first and last frames arrive
  const h = t.finish();
  assert.ok(h);
  assert.equal(h.framesExpected, 50);
  assert.equal(h.framesReceived, 34);
});

test("finish resets for the next utterance", () => {
  const t = new UtteranceTracker();
  t.frame(0);
  t.finish();
  t.frame(5000);
  assert.deepEqual(t.finish(), { framesReceived: 1, framesExpected: 1 });
});
