import assert from "node:assert/strict";
import { test } from "node:test";
import { loadConfig } from "../src/config.js";

test("builds the core URL with defaults", () => {
  const c = loadConfig({ DISCORD_TOKEN: "t", EARS_SHARED_SECRET: "s" });
  assert.equal(c.coreUrl, "ws://127.0.0.1:8765");
});

test("audio debug logging is off unless DMBOT_DEBUG_AUDIO=1", () => {
  const base = { DISCORD_TOKEN: "t", EARS_SHARED_SECRET: "s" };
  assert.equal(loadConfig(base).debugAudio, false);
  assert.equal(loadConfig({ ...base, DMBOT_DEBUG_AUDIO: "0" }).debugAudio, false);
  assert.equal(loadConfig({ ...base, DMBOT_DEBUG_AUDIO: "1" }).debugAudio, true);
});

test("names every missing setting", () => {
  assert.throws(() => loadConfig({}), /DISCORD_TOKEN, EARS_SHARED_SECRET/);
});

test("rejects a non-numeric port", () => {
  assert.throws(
    () => loadConfig({ DISCORD_TOKEN: "t", EARS_SHARED_SECRET: "s", EARS_WS_PORT: "abc" }),
    /must be a number/,
  );
});

test("shard and log settings default to one shard and text logs", () => {
  const c = loadConfig({ DISCORD_TOKEN: "t", EARS_SHARED_SECRET: "s" });
  assert.deepEqual(c.shards, { count: 1, ids: [0] });
  assert.equal(c.logFormat, "text");
  assert.equal(c.logLevel, "INFO");
});

test("shard and log settings are read", () => {
  const c = loadConfig({
    DISCORD_TOKEN: "t",
    EARS_SHARED_SECRET: "s",
    SHARD_COUNT: "4",
    SHARD_IDS: "2,3",
    LOG_FORMAT: "json",
    LOG_LEVEL: "warning",
  });
  assert.deepEqual(c.shards, { count: 4, ids: [2, 3] });
  assert.equal(c.logFormat, "json");
  assert.equal(c.logLevel, "WARNING");
});
