import assert from "node:assert/strict";
import { test } from "node:test";
import { noteVoiceMembers, type MemberNotes, type VoiceMember } from "../src/voiceMembers.js";

function notes(): MemberNotes & { noted: [string, boolean][] } {
  const noted: [string, boolean][] = [];
  return {
    channelId: "c1",
    noted,
    noteMember: (id, isBot) => noted.push([id, isBot]),
    knows: (id) => noted.some(([known]) => known === id),
  };
}

const person = { user: { bot: false } };
const bot = { user: { bot: true } };
const everyone = (): boolean => true;
const noLookup = (): Promise<undefined> => assert.fail("no lookup expected");
const flush = (): Promise<void> => new Promise((resolve) => setImmediate(resolve));

test("cached members in the channel are noted at once, without a lookup", () => {
  const session = notes();
  const states: VoiceMember[] = [
    { id: "1", channelId: "c1", member: person },
    { id: "2", channelId: "c1", member: bot },
  ];
  noteVoiceMembers(session, states, () => false, noLookup); // consent doesn't matter here
  assert.deepEqual(session.noted, [
    ["1", false],
    ["2", true],
  ]);
});

test("people in other channels, or who left, are ignored", () => {
  const session = notes();
  const states: VoiceMember[] = [
    { id: "1", channelId: "other", member: person },
    { id: "2", channelId: null, member: null },
  ];
  noteVoiceMembers(session, states, everyone, noLookup);
  assert.deepEqual(session.noted, []);
});

test("a member discord.js hasn't cached is looked up in the background", async () => {
  const session = notes();
  const looked: string[] = [];
  const states: VoiceMember[] = [
    { id: "1", channelId: "c1", member: null },
    { id: "2", channelId: "c1", member: null },
    { id: "3", channelId: "c1", member: null },
  ];
  noteVoiceMembers(session, states, everyone, (id) => {
    looked.push(id);
    if (id === "1") return Promise.resolve(false);
    if (id === "2") return Promise.resolve(undefined); // can't be found
    return Promise.reject(new Error("Discord is down"));
  });
  await flush();
  assert.deepEqual(looked, ["1", "2", "3"]);
  assert.deepEqual(session.noted, [["1", false]]); // the others stay unknown
});

test("nobody who hasn't opted in is looked up", () => {
  const session = notes();
  noteVoiceMembers(session, [{ id: "1", channelId: "c1", member: null }], () => false, noLookup);
  assert.deepEqual(session.noted, []);
});

test("someone already known isn't looked up again when they mute or deafen", () => {
  const session = notes();
  session.noteMember("1", false);
  noteVoiceMembers(session, [{ id: "1", channelId: "c1", member: null }], everyone, noLookup);
  assert.deepEqual(session.noted, [["1", false]]);
});
