import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import {
  AUDIO_FRAME_KIND,
  AUDIO_HEADER_BYTES,
  decodeAudioFrame,
  encodeAudioFrame,
  parseCoreCommand,
} from "../src/protocol.js";

interface Fixtures {
  audioFrameHeaderBytes: number;
  audioFrameKind: number;
  audioFrames: { guildId: string; userId: string; timestampMs: number; samples: number[]; hex: string }[];
}
const fixtures = JSON.parse(
  readFileSync(new URL("../../protocol/fixtures.json", import.meta.url), "utf8"),
) as Fixtures;

function pcmFrom(samples: number[]): Buffer {
  const pcm = Buffer.alloc(samples.length * 2);
  samples.forEach((s, i) => pcm.writeInt16LE(s, i * 2));
  return pcm;
}

test("constants match shared fixtures", () => {
  assert.equal(AUDIO_HEADER_BYTES, fixtures.audioFrameHeaderBytes);
  assert.equal(AUDIO_FRAME_KIND, fixtures.audioFrameKind);
});

test("encodeAudioFrame matches shared fixture bytes", () => {
  for (const f of fixtures.audioFrames) {
    assert.equal(encodeAudioFrame(f.guildId, f.userId, f.timestampMs, pcmFrom(f.samples)).toString("hex"), f.hex);
  }
});

test("decodeAudioFrame round-trips the fixture", () => {
  for (const f of fixtures.audioFrames) {
    const decoded = decodeAudioFrame(Buffer.from(f.hex, "hex"));
    assert.ok(decoded);
    assert.equal(decoded.guildId, f.guildId);
    assert.equal(decoded.userId, f.userId);
    assert.equal(decoded.timestampMs, f.timestampMs);
    assert.deepEqual(decoded.pcm, pcmFrom(f.samples));
  }
});

test("decodeAudioFrame rejects short, wrong-kind and odd-length frames", () => {
  assert.equal(decodeAudioFrame(Buffer.alloc(5)), null);
  const wrongKind = Buffer.alloc(AUDIO_HEADER_BYTES + 2);
  wrongKind.writeUInt8(9, 0);
  assert.equal(decodeAudioFrame(wrongKind), null);
  const odd = Buffer.alloc(AUDIO_HEADER_BYTES + 3);
  odd.writeUInt8(AUDIO_FRAME_KIND, 0);
  assert.equal(decodeAudioFrame(odd), null);
});

test("snowflakes larger than 2^53 survive encoding", () => {
  const big = "18446744073709551615"; // uint64 max
  const decoded = decodeAudioFrame(encodeAudioFrame(big, big, 1, Buffer.alloc(0)));
  assert.equal(decoded?.guildId, big);
  assert.equal(decoded?.userId, big);
});

test("parseCoreCommand accepts valid commands", () => {
  assert.deepEqual(parseCoreCommand('{"type":"join","guildId":"1","channelId":"2"}'), {
    type: "join",
    guildId: "1",
    channelId: "2",
  });
  assert.deepEqual(parseCoreCommand('{"type":"leave","guildId":"1"}'), { type: "leave", guildId: "1" });
  assert.deepEqual(parseCoreCommand('{"type":"allowlist","guildId":"1","userIds":["3","4"]}'), {
    type: "allowlist",
    guildId: "1",
    userIds: ["3", "4"],
  });
});

test("parseCoreCommand rejects malformed input", () => {
  for (const raw of [
    "not json",
    "null",
    "[]",
    '{"type":"join","guildId":"1"}',
    '{"type":"join","guildId":"abc","channelId":"2"}',
    '{"type":"allowlist","guildId":"1","userIds":[3]}',
    '{"type":"allowlist","guildId":"1","userIds":"3"}',
    '{"type":"explode"}',
  ]) {
    assert.equal(parseCoreCommand(raw), null, raw);
  }
});
