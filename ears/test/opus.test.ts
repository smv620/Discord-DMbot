import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { once } from "node:events";
import { test } from "node:test";
import prism from "prism-media";

const require = createRequire(import.meta.url);

test("decodes Opus with opusscript and no native addon", async () => {
  assert.throws(() => require.resolve("@discordjs/opus"), { code: "MODULE_NOT_FOUND" });

  const encoder = new prism.opus.Encoder({ rate: 48_000, channels: 2, frameSize: 960 });
  const decoder = new prism.opus.Decoder({ rate: 48_000, channels: 2, frameSize: 960 });
  encoder.pipe(decoder);

  const output = once(decoder, "data");
  encoder.end(Buffer.alloc(960 * 2 * 2));
  const [pcm] = await output;

  assert.ok(Buffer.isBuffer(pcm));
  assert.equal(pcm.length, 960 * 2 * 2);

  encoder.destroy();
  decoder.destroy();
});
