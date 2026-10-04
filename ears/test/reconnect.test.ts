import assert from "node:assert/strict";
import { test } from "node:test";
import { MAX_BACKOFF_MS, MIN_BACKOFF_MS, ReconnectPolicy, REJECTED_CLOSE_CODE, STABLE_MS } from "../src/reconnect.js";

test("backs off exponentially up to the maximum", () => {
  const p = new ReconnectPolicy();
  const delays = Array.from({ length: 8 }, () => p.closed(0, 1006).delayMs);
  assert.deepEqual(delays, [500, 1000, 2000, 4000, 8000, 15000, 15000, 15000]);
});

test("a connection that opens and drops at once does not reset the backoff", () => {
  const p = new ReconnectPolicy();
  p.closed(0, 1006); // 500
  p.closed(0, 1006); // 1000
  p.opened(1000);
  assert.equal(p.closed(1100, 1006).delayMs, 2000);
});

test("a connection that stayed up resets the backoff", () => {
  const p = new ReconnectPolicy();
  for (let i = 0; i < 5; i++) p.closed(0, 1006);
  p.opened(0);
  assert.equal(p.closed(STABLE_MS, 1006).delayMs, MIN_BACKOFF_MS);
});

test("a refusal from core waits the maximum, even right after opening", () => {
  const p = new ReconnectPolicy();
  p.opened(0);
  assert.deepEqual(p.closed(5, REJECTED_CLOSE_CODE), { delayMs: MAX_BACKOFF_MS, rejected: true });
  p.opened(20_000);
  assert.deepEqual(p.closed(20_010, REJECTED_CLOSE_CODE), { delayMs: MAX_BACKOFF_MS, rejected: true });
});
