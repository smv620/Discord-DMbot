import assert from "node:assert/strict";
import { test } from "node:test";
import { Logger, parseLogFormat, parseLogLevel } from "../src/log.js";

function capture(format: "text" | "json", level: "DEBUG" | "INFO" | "WARNING" | "ERROR" = "INFO") {
  const lines: string[] = [];
  const log = new Logger({ format, level, shards: { count: 2, ids: [0, 1] }, write: (l) => lines.push(l) });
  return { log, lines };
}

test("json lines carry shard and server IDs as text", () => {
  const { log, lines } = capture("json");
  log.info("joined voice", { guildId: "81384788765712384" });
  const entry = JSON.parse(lines[0] ?? "") as Record<string, unknown>;
  assert.equal(entry.level, "INFO");
  assert.equal(entry.logger, "ears");
  assert.equal(entry.msg, "joined voice");
  assert.equal(entry.guild_id, "81384788765712384");
  assert.equal(entry.shard_id, 0);
  assert.match(String(entry.ts), /^\d{4}-\d\d-\d\dT/);
});

test("no shard is guessed when several are served and no server is known", () => {
  const { log, lines } = capture("json");
  log.warn("core link down");
  const entry = JSON.parse(lines[0] ?? "") as Record<string, unknown>;
  assert.equal(entry.shard_id, undefined);
});

test("errors are included", () => {
  const { log, lines } = capture("json");
  log.error("failed", { error: new Error("boom") });
  assert.match(String((JSON.parse(lines[0] ?? "") as Record<string, unknown>).exc), /boom/);
});

test("text format shows context in brackets", () => {
  const { log, lines } = capture("text");
  log.info("hello", { guildId: "81384788765712384" });
  assert.match(lines[0] ?? "", / INFO ears \[shard=0 guild=81384788765712384\]: hello$/);
});

test("level filter", () => {
  const { log, lines } = capture("json", "WARNING");
  log.info("quiet");
  log.debug("quieter");
  log.warn("loud");
  assert.equal(lines.length, 1);
});

test("settings parsing", () => {
  assert.equal(parseLogFormat(undefined), "text");
  assert.equal(parseLogFormat("JSON"), "json");
  assert.throws(() => parseLogFormat("xml"), /LOG_FORMAT/);
  assert.equal(parseLogLevel("debug"), "DEBUG");
  assert.throws(() => parseLogLevel("loud"), /LOG_LEVEL/);
});
