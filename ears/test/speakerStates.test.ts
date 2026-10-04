import assert from "node:assert/strict";
import { test } from "node:test";
import { SpeakerStates } from "../src/speakerStates.js";

test("only changes are logged", () => {
  const states = new SpeakerStates();
  assert.equal(states.note("1", true), "capturing user 1");
  assert.equal(states.note("1", true), null); // every new utterance: no line
  assert.equal(states.note("1", false, "opted out"), "not capturing user 1: opted out");
  assert.equal(states.note("1", false, "opted out"), null);
  assert.equal(states.note("1", true), "capturing user 1"); // opted in again
});

test("users are tracked separately", () => {
  const states = new SpeakerStates();
  assert.equal(states.note("1", true), "capturing user 1");
  assert.equal(states.note("2", false, "not opted in, or a bot"), "not capturing user 2: not opted in, or a bot");
  assert.equal(states.note("1", true), null);
});

test("the reason is optional", () => {
  assert.equal(new SpeakerStates().note("3", false), "not capturing user 3");
});
