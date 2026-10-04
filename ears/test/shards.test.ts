import assert from "node:assert/strict";
import { test } from "node:test";
import { parseShards, shardFor } from "../src/shards.js";

test("defaults to one shard", () => {
  assert.deepEqual(parseShards(undefined, undefined), { count: 1, ids: [0] });
  assert.deepEqual(parseShards(" ", ""), { count: 1, ids: [0] });
});

test("count alone means all shards", () => {
  assert.deepEqual(parseShards("3", ""), { count: 3, ids: [0, 1, 2] });
});

test("explicit ids are sorted", () => {
  assert.deepEqual(parseShards("4", " 3, 1 "), { count: 4, ids: [1, 3] });
});

test("rejects bad settings with readable messages", () => {
  assert.throws(() => parseShards("zero", ""), /SHARD_COUNT must be a whole number/);
  assert.throws(() => parseShards("0", ""), /between 1 and/);
  assert.throws(() => parseShards("2", "2"), /SHARD_COUNT is 2/);
  assert.throws(() => parseShards("2", "1,1"), /twice/);
  assert.throws(() => parseShards("2", "a"), /separated by commas/);
  assert.throws(() => parseShards("2", "-1"), /separated by commas/);
});

test("shard rule matches Discord's (guild >> 22) % count", () => {
  // Same vectors as core/tests/test_sharding.py.
  assert.equal(shardFor("81384788765712384", 1), 0);
  assert.equal(shardFor("81384788765712384", 2), 0);
  assert.equal(shardFor("81384788765712384", 7), 3);
  assert.equal(shardFor("18446744073709551615", 16), 15);
});
