import assert from "node:assert/strict";
import { afterEach, beforeEach, mock, test } from "node:test";
import { Allowlist } from "../src/consent.js";
import { UNKNOWN_RETRY_MS } from "../src/voice.js";
import { applyConsentList, noteVoiceMembers, type MemberNotes, type VoiceMember } from "../src/voiceMembers.js";

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

beforeEach(() => mock.timers.enable({ apis: ["Date"], now: 1_000_000 }));
afterEach(() => mock.timers.reset());

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

test("a second note while a lookup is running doesn't ask Discord again", async () => {
  const session = notes();
  const looked: string[] = [];
  let answer: (isBot: boolean) => void = () => undefined;
  const lookUp = (id: string): Promise<boolean> => {
    looked.push(id);
    return new Promise((resolve) => (answer = resolve));
  };
  const states: VoiceMember[] = [{ id: "1", channelId: "c1", member: null }];
  noteVoiceMembers(session, states, everyone, lookUp);
  noteVoiceMembers(session, states, everyone, lookUp);
  assert.deepEqual(looked, ["1"]);
  answer(false);
  await flush();
  assert.deepEqual(session.noted, [["1", false]]);
});

test("someone Discord couldn't find isn't looked up again on every mute, only later", async () => {
  const session = notes();
  const looked: string[] = [];
  const lookUp = (id: string): Promise<undefined> => {
    looked.push(id);
    return id === "2" ? Promise.reject(new Error("Discord is down")) : Promise.resolve(undefined);
  };
  const states: VoiceMember[] = [
    { id: "1", channelId: "c1", member: null },
    { id: "2", channelId: "c1", member: null },
  ];
  noteVoiceMembers(session, states, everyone, lookUp);
  await flush();
  mock.timers.tick(UNKNOWN_RETRY_MS - 1);
  noteVoiceMembers(session, states, everyone, lookUp); // a mute, a consent push...
  assert.deepEqual(looked, ["1", "2"]);
  mock.timers.tick(1);
  noteVoiceMembers(session, states, everyone, lookUp);
  assert.deepEqual(looked, ["1", "2", "1", "2"]);
});

function table(): MemberNotes & { noted: [string, boolean][]; events: string[]; dropSpeakers(ids: readonly string[]): void } {
  const events: string[] = [];
  const base = notes();
  return {
    ...base,
    noted: base.noted,
    events,
    noteMember: (id, isBot) => {
      events.push(`note ${id}`);
      base.noteMember(id, isBot);
    },
    dropSpeakers: (ids) => events.push(`drop ${ids.join(",")}`),
  };
}

test("a new consent list drops whoever left, then looks up whoever joined", async () => {
  const allowlist = new Allowlist();
  allowlist.set("g", ["1", "2"]);
  const session = table();
  const looked: string[] = [];
  const states: VoiceMember[] = [
    { id: "2", channelId: "c1", member: null }, // removed in this push
    { id: "3", channelId: "c1", member: null }, // just opted in
    { id: "4", channelId: "other", member: null }, // another channel
  ];
  const removed = applyConsentList(allowlist, "g", ["1", "3", "4"], session, states, (id) => {
    looked.push(id);
    return Promise.resolve(false);
  });
  await flush();
  assert.deepEqual(removed, ["2"]);
  assert.deepEqual(looked, ["3"]);
  assert.deepEqual(session.events, ["drop 2", "note 3"]);
  assert.equal(allowlist.isAllowed("g", "3", false), true);
});

test("a consent list with no session, or no cached server, still replaces the list", () => {
  const allowlist = new Allowlist();
  applyConsentList(allowlist, "g", ["1"], undefined, [{ id: "1", channelId: "c1", member: null }], noLookup);
  assert.equal(allowlist.isAllowed("g", "1", false), true);
  const session = table();
  applyConsentList(allowlist, "g", [], session, undefined, noLookup);
  assert.deepEqual(session.events, ["drop 1"]);
  assert.equal(allowlist.isAllowed("g", "1", false), false);
});
