import assert from "node:assert/strict";
import { test } from "node:test";
import { Downsampler } from "../src/audio.js";

/** Build 48 kHz stereo PCM from [left, right] frame pairs. */
function stereo(frames: [number, number][]): Buffer {
  const buf = Buffer.alloc(frames.length * 4);
  frames.forEach(([l, r], i) => {
    buf.writeInt16LE(l, i * 4);
    buf.writeInt16LE(r, i * 4 + 2);
  });
  return buf;
}

function samples(buf: Buffer): number[] {
  const out: number[] = [];
  for (let i = 0; i < buf.length; i += 2) out.push(buf.readInt16LE(i));
  return out;
}

test("averages 3 stereo frames into 1 mono sample", () => {
  const out = new Downsampler().push(stereo([[300, 0], [600, 0], [900, 0]]));
  assert.deepEqual(samples(out), [300]);
});

test("one 20 ms Discord frame (960 stereo frames) becomes 320 samples", () => {
  const frames: [number, number][] = Array.from({ length: 960 }, () => [1000, -1000]);
  const out = new Downsampler().push(stereo(frames));
  assert.equal(out.length, 320 * 2);
  assert.ok(samples(out).every((s) => s === 0));
});

test("keeps leftovers between chunks without losing audio", () => {
  const ds = new Downsampler();
  const whole = stereo([[3, 3], [3, 3], [3, 3], [6, 6], [6, 6], [6, 6]]);
  const a = ds.push(whole.subarray(0, 7)); // splits mid-sample on purpose
  const b = ds.push(whole.subarray(7));
  assert.deepEqual([...samples(a), ...samples(b)], [3, 6]);
});

test("stays within int16 range at full scale", () => {
  const out = new Downsampler().push(stereo([[32767, 32767], [32767, 32767], [32767, 32767]]));
  assert.deepEqual(samples(out), [32767]);
  const neg = new Downsampler().push(stereo([[-32768, -32768], [-32768, -32768], [-32768, -32768]]));
  assert.deepEqual(samples(neg), [-32768]);
});

test("reset discards leftovers", () => {
  const ds = new Downsampler();
  ds.push(Buffer.alloc(5));
  ds.reset();
  assert.equal(ds.push(stereo([[9, 9], [9, 9], [9, 9]])).readInt16LE(0), 9);
});
