import assert from "node:assert/strict";
import { test } from "node:test";
import { Allowlist } from "../src/consent.js";

test("denies by default", () => {
  assert.equal(new Allowlist().isAllowed("g", "u", false), false);
});

test("allows listed humans, never bots", () => {
  const list = new Allowlist();
  list.set("g", ["u1"]);
  assert.equal(list.isAllowed("g", "u1", false), true);
  assert.equal(list.isAllowed("g", "u1", true), false);
  assert.equal(list.isAllowed("g", "u2", false), false);
});

test("lists are per guild", () => {
  const list = new Allowlist();
  list.set("g1", ["u"]);
  assert.equal(list.isAllowed("g2", "u", false), false);
});

test("set replaces the list and reports removed users", () => {
  const list = new Allowlist();
  list.set("g", ["a", "b", "c"]);
  assert.deepEqual(list.set("g", ["b"]).sort(), ["a", "c"]);
  assert.equal(list.isAllowed("g", "a", false), false);
});

test("clear removes everyone", () => {
  const list = new Allowlist();
  list.set("g", ["a"]);
  list.clear("g");
  assert.equal(list.isAllowed("g", "a", false), false);
});
