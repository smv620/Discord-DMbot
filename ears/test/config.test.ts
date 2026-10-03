import assert from "node:assert/strict";
import { test } from "node:test";
import { loadConfig } from "../src/config.js";

test("builds the core URL with defaults", () => {
  const c = loadConfig({ DISCORD_TOKEN: "t", EARS_SHARED_SECRET: "s" });
  assert.equal(c.coreUrl, "ws://127.0.0.1:8765");
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
