import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import { Readable, Transform } from "node:stream";
import { afterEach, beforeEach, mock, test } from "node:test";
import { VoiceReceiver, type DiscordGatewayAdapterCreator, type VoiceConnection } from "@discordjs/voice";
import { Allowlist } from "../src/consent.js";
import { Logger } from "../src/log.js";
import type { EarsMessage } from "../src/protocol.js";
import {
  RESUBSCRIBE_DELAYS_MS,
  RESUBSCRIBE_LIMIT,
  TableSession,
  UNKNOWN_RETRY_MS,
  WATCHDOG_MS,
  type BotLookup,
} from "../src/voice.js";
import { applyConsentList } from "../src/voiceMembers.js";

const GUILD = "111";
const CHANNEL = "222";
const ALICE = "1001"; // opted in
const BOB = "1002"; // not opted in
const CAROL = "1003"; // on the list, but noted as a bot
const DAVE = "1004"; // never seen, not opted in
const SPEECH = Buffer.from([0x78, 0x01, 0x02]); // any non-silence Opus packet
const SILENCE = Buffer.from([0xf8, 0xff, 0xfe]); // Discord's Opus silence frame

/**
 * Behaves like @discordjs/voice's VoiceReceiver.onUdpMessage: each packet first tells
 * `speaking` (which emits "start" for a new speaker), then goes to the user's
 * subscription, or is dropped if there is none yet.
 */
class FakeReceiver {
  /** Like the library's SpeakingMap: "start" events, and who is sending right now. */
  readonly speaking = Object.assign(new EventEmitter(), { users: new Map<string, number>() });
  readonly subscriptions = new Map<string, Readable>();
  readonly subscribed: string[] = [];
  private readonly talking = new Set<string>();

  subscribe(userId: string): Readable {
    const stream = new Readable({ objectMode: true, read() {} });
    stream.once("close", () => this.subscriptions.delete(userId));
    this.subscriptions.set(userId, stream);
    this.subscribed.push(userId);
    return stream;
  }

  packet(userId: string, packet = SPEECH): void {
    this.sending(userId);
    this.subscriptions.get(userId)?.push(packet);
  }

  /** A packet arrives but the library can't decrypt it: no data for the stream. */
  sending(userId: string): void {
    if (!this.talking.has(userId)) {
      this.talking.add(userId);
      this.speaking.users.set(userId, Date.now());
      this.speaking.emit("start", userId);
    }
  }

  /** The library ends the stream (800 ms since a packet it could decrypt) while they send. */
  endWhileSending(userId: string): void {
    this.subscriptions.get(userId)?.push(null);
  }

  /** No more packets (the speaking map's 100 ms ran out), with no stream end. */
  quiet(userId: string): void {
    this.talking.delete(userId);
    this.speaking.users.delete(userId);
  }

  /** The library errors the stream, as @discordjs/voice does when a packet won't decrypt. */
  fail(userId: string, message = "Failed to decrypt: DecryptionFailed(UnencryptedWhenPassthroughDisabled)"): void {
    this.subscriptions.get(userId)?.destroy(new Error(message));
  }

  /** The speaker goes quiet; their stream ends as it would after the silence timeout. */
  stop(userId: string): void {
    this.quiet(userId);
    this.subscriptions.get(userId)?.push(null);
  }
}

interface Harness {
  session: TableSession;
  allowlist: Allowlist;
  receiver: FakeReceiver;
  sent: EarsMessage[];
  /** Audio frames sent to core. */
  audio: Buffer[];
  lookups: string[];
}

/** Stands in for the Opus decoder: 20 ms of 48 kHz stereo silence per packet. */
function fakeDecoder(): Transform {
  return new Transform({
    transform(_packet: Buffer, _encoding, done) {
      done(null, Buffer.alloc(960 * 2 * 2));
    },
  });
}

interface Extra {
  debugAudio?: boolean;
  /** Log lines written, if given. */
  logLines?: string[];
  /** The connection's "debug" listeners, if given. */
  debugListeners?: ((message: string) => void)[];
}

function harness(
  lookUpBot: BotLookup = () => Promise.resolve(false),
  cached: ReadonlyMap<string, boolean> = new Map(),
  extra: Extra = {},
): Harness {
  const receiver = new FakeReceiver();
  const sent: EarsMessage[] = [];
  const audio: Buffer[] = [];
  const lookups: string[] = [];
  const connection = {
    receiver,
    on: (event: string, listener: (message: string) => void) => {
      if (event === "debug") extra.debugListeners?.push(listener);
      return connection;
    },
    state: { status: "ready" },
    destroy: () => undefined,
  };
  const allowlist = new Allowlist();
  allowlist.set(GUILD, [ALICE, CAROL]);
  const session = new TableSession({
    guildId: GUILD,
    channelId: CHANNEL,
    adapterCreator: (() => ({})) as unknown as DiscordGatewayAdapterCreator,
    allowlist,
    link: { send: (m) => sent.push(m), sendAudio: (frame) => audio.push(frame), droppedAudioFrames: 0 },
    peekBot: (userId) => cached.get(userId),
    lookUpBot: (userId) => {
      lookups.push(userId);
      return lookUpBot(userId);
    },
    debugAudio: extra.debugAudio,
    log: new Logger({
      format: "text",
      level: extra.logLines ? "INFO" : "ERROR",
      shards: { count: 1, ids: [0] },
      write: (line: string) => extra.logLines?.push(line),
    }),
    // The fake has just the parts TableSession uses.
    connect: () => connection as unknown as VoiceConnection,
    createDecoder: fakeDecoder,
  });
  return { session, allowlist, receiver, sent, audio, lookups };
}

/** Let stream events run, then move the clock one Opus frame on. */
async function nextFrame(): Promise<void> {
  await new Promise((resolve) => setImmediate(resolve));
  mock.timers.tick(20);
}

async function speak(h: Harness, userId: string, packets: number): Promise<void> {
  for (let i = 0; i < packets; i++) {
    h.receiver.packet(userId);
    await nextFrame();
  }
}

async function stop(h: Harness, userId: string): Promise<void> {
  h.receiver.stop(userId);
  await nextFrame();
}

function health(sent: EarsMessage[]): { framesReceived: number; framesExpected: number }[] {
  return sent.flatMap((m) =>
    m.type === "health" ? [{ framesReceived: m.framesReceived, framesExpected: m.framesExpected }] : [],
  );
}

beforeEach(() => mock.timers.enable({ apis: ["Date", "setTimeout"], now: 1_000_000 }));
afterEach(() => mock.timers.reset());

test("a known person's speech is kept from the first packet", async () => {
  const h = harness();
  h.session.noteMember(ALICE, false);
  await speak(h, ALICE, 10);
  await stop(h, ALICE);
  assert.deepEqual(health(h.sent), [{ framesReceived: 10, framesExpected: 10 }]);
  assert.equal(h.audio.length, 10); // and every packet's audio went to core
  assert.deepEqual(h.lookups, []); // the noted member was used, not a lookup
});

test("a member discord.js has cached is captured from the first packet, without a lookup", async () => {
  const h = harness(undefined, new Map([[ALICE, false]]));
  await speak(h, ALICE, 4);
  await stop(h, ALICE);
  assert.deepEqual(health(h.sent), [{ framesReceived: 4, framesExpected: 4 }]);
  assert.deepEqual(h.lookups, []);
});

test("speech that starts again before the stream ends stays one subscription", async () => {
  const h = harness();
  h.session.noteMember(ALICE, false);
  await speak(h, ALICE, 3);
  h.receiver.speaking.emit("start", ALICE); // Discord's speaking flag restarts after 100 ms
  await speak(h, ALICE, 3);
  await stop(h, ALICE);
  assert.deepEqual(h.receiver.subscribed, [ALICE]);
  assert.deepEqual(health(h.sent), [{ framesReceived: 6, framesExpected: 6 }]);
});

test("every piece of speech after a pause is kept whole", async () => {
  const h = harness();
  h.session.noteMember(ALICE, false);
  await speak(h, ALICE, 5);
  await stop(h, ALICE);
  await speak(h, ALICE, 7);
  await stop(h, ALICE);
  assert.deepEqual(health(h.sent), [
    { framesReceived: 5, framesExpected: 5 },
    { framesReceived: 7, framesExpected: 7 },
  ]);
});

test("waiting on a lookup loses the packets that arrive meanwhile (the old path for everyone)", async () => {
  let answer: (isBot: boolean) => void = () => undefined;
  const h = harness(() => new Promise((resolve) => (answer = resolve)));
  await speak(h, ALICE, 4); // never seen: these arrive while Discord is asked
  assert.deepEqual(h.receiver.subscribed, []);
  answer(false);
  await nextFrame();
  await speak(h, ALICE, 6);
  await stop(h, ALICE);
  assert.deepEqual(health(h.sent), [{ framesReceived: 6, framesExpected: 6 }]); // 4 lost
  assert.deepEqual(h.lookups, [ALICE]);
});

test("someone looked up once is known from then on", async () => {
  const h = harness();
  await speak(h, ALICE, 2);
  await stop(h, ALICE);
  await speak(h, ALICE, 3);
  await stop(h, ALICE);
  assert.deepEqual(h.lookups, [ALICE]);
  assert.deepEqual(health(h.sent).at(-1), { framesReceived: 3, framesExpected: 3 });
});

test("a lookup that says bot is remembered, and the bot is never captured", async () => {
  const h = harness(() => Promise.resolve(true));
  await speak(h, ALICE, 2);
  await stop(h, ALICE);
  await speak(h, ALICE, 2);
  assert.deepEqual(h.lookups, [ALICE]);
  assert.deepEqual(h.receiver.subscribed, []);
  assert.deepEqual(h.sent, []);
});

test("a bot is never subscribed to, even if it is on the consent list", async () => {
  const h = harness();
  h.session.noteMember(CAROL, true); // on the list, but a bot
  await speak(h, CAROL, 5);
  await stop(h, CAROL);
  assert.deepEqual(h.receiver.subscribed, []);
  assert.deepEqual(h.sent, []);
});

test("someone not on the consent list is never subscribed to or looked up", async () => {
  const h = harness();
  h.session.noteMember(BOB, false);
  await speak(h, BOB, 5); // known person, not opted in
  await speak(h, DAVE, 5); // never seen, not opted in
  assert.deepEqual(h.receiver.subscribed, []);
  assert.deepEqual(h.lookups, []);
  assert.deepEqual(h.sent, []);
});

test("someone who can't be found is treated as a bot, and asked about again later", async () => {
  const h = harness(() => Promise.resolve(undefined));
  await speak(h, ALICE, 3);
  await stop(h, ALICE);
  await speak(h, ALICE, 3);
  await stop(h, ALICE);
  assert.deepEqual(h.lookups, [ALICE]); // not asked again straight away
  mock.timers.tick(UNKNOWN_RETRY_MS);
  await speak(h, ALICE, 1);
  assert.deepEqual(h.lookups, [ALICE, ALICE]);
  assert.deepEqual(h.receiver.subscribed, []);
});

test("a lookup that fails is treated as a bot, not a crash", async () => {
  const h = harness(() => Promise.reject(new Error("Discord is down")));
  await speak(h, ALICE, 3);
  assert.deepEqual(h.receiver.subscribed, []);
});

test("a session that ends during a lookup captures nothing", async () => {
  let answer: (isBot: boolean) => void = () => undefined;
  const h = harness(() => new Promise((resolve) => (answer = resolve)));
  await speak(h, ALICE, 1);
  h.session.destroy();
  answer(false);
  await nextFrame();
  assert.deepEqual(h.receiver.subscribed, []);
  assert.deepEqual(h.sent, []);
});

test("consent withdrawn during a lookup stops the capture", async () => {
  let answer: (isBot: boolean) => void = () => undefined;
  const h = harness(() => new Promise((resolve) => (answer = resolve)));
  await speak(h, ALICE, 1);
  h.allowlist.set(GUILD, [CAROL]); // opts out while Discord is asked
  answer(false);
  await nextFrame();
  assert.deepEqual(h.receiver.subscribed, []);
});

test("nothing is noted or captured after the session ends", async () => {
  const h = harness();
  h.session.destroy();
  h.session.noteMember(ALICE, false);
  await speak(h, ALICE, 3);
  assert.deepEqual(h.receiver.subscribed, []);
});

test("someone who opts in mid-session is looked up then, and captured from their first packet", async () => {
  const h = harness();
  const inVoice = [{ id: BOB, channelId: CHANNEL, member: null }]; // not in discord.js's cache
  const background: string[] = [];
  const consentList = (userIds: string[]): string[] =>
    applyConsentList(h.allowlist, GUILD, userIds, h.session, inVoice, (userId) => {
      background.push(userId);
      return Promise.resolve(false);
    });
  consentList([ALICE, CAROL]); // BOB hasn't opted in: not looked up
  assert.deepEqual(background, []);
  consentList([ALICE, BOB, CAROL]); // core sends the new list
  await nextFrame();
  assert.deepEqual(background, [BOB]);
  await speak(h, BOB, 5);
  await stop(h, BOB);
  assert.deepEqual(health(h.sent), [{ framesReceived: 5, framesExpected: 5 }]);
  assert.deepEqual(h.lookups, []); // nothing to ask when they spoke
});

test("the real receiver announces a new speaker before routing their packet", () => {
  // Pins @discordjs/voice's VoiceReceiver.onUdpMessage order: speaking.onPacket()
  // emits "start", then the packet goes to subscriptions.get(userId). TableSession
  // relies on it to keep the first packet. Written against @discordjs/voice 0.19.2; if
  // an upgrade fails this, onSpeakingStart's synchronous subscribe no longer works.
  const receiver = new VoiceReceiver({} as unknown as VoiceConnection); // only stored
  receiver.ssrcMap.update({ audioSSRC: 42, userId: ALICE });
  const seen: string[] = [];
  const get = receiver.subscriptions.get.bind(receiver.subscriptions);
  receiver.subscriptions.get = (userId) => {
    seen.push(receiver.subscriptions.has(userId) ? "routed, subscribed" : "routed, not subscribed");
    return get(userId);
  };
  receiver.speaking.on("start", () => {
    seen.push("start");
    receiver.subscribe(ALICE).destroy();
  });
  const packet = Buffer.alloc(20);
  packet.writeUInt32BE(42, 8); // the SSRC
  receiver.onUdpMessage(packet);
  assert.equal(seen[0], "start");
  assert.equal(seen.at(-1), "routed, subscribed");
});

// ---- receive errors (#631) ----------------------------------------------------

async function fail(h: Harness, userId: string, message?: string): Promise<void> {
  h.receiver.fail(userId, message);
  await nextFrame(); // the stream closes, and ears listens again
}

function warnings(sent: EarsMessage[]): EarsMessage[] {
  return sent.filter((m) => m.type === "status" && m.state === "warning");
}

function ends(sent: EarsMessage[]): number {
  return sent.filter((m) => m.type === "speaking" && m.event === "end").length;
}

test("after a decrypt error ears listens again at once, and counts what was lost", async () => {
  const h = harness();
  h.session.noteMember(ALICE, false);
  await speak(h, ALICE, 1);
  mock.timers.tick(720); // the library drops 37 packets without a word, then errors
  await fail(h, ALICE);
  await speak(h, ALICE, 5); // still talking: no pause, no new "start"
  await stop(h, ALICE);
  // 1 heard, 37 lost (the 740 ms since it), 5 heard again.
  assert.deepEqual(health(h.sent), [{ framesReceived: 6, framesExpected: 43 }]);
  assert.equal(h.audio.length, 6); // the speech after the error reached core
  assert.deepEqual(h.receiver.subscribed, [ALICE, ALICE]);
  assert.deepEqual(warnings(h.sent), []);
});

/** A later try: ears waits its step (the `step`th try in a row, from 0) before listening again. */
async function failAndWait(h: Harness, userId: string, step: number): Promise<void> {
  const wait = RESUBSCRIBE_DELAYS_MS[step];
  assert.ok(wait !== undefined, `no step ${step}`);
  await fail(h, userId);
  mock.timers.tick(wait);
  await nextFrame();
}

test("errors back to back at the first packet cost a tenth of a second, not a second (#761)", async () => {
  // dev1's Test B: after an MLS welcome at transition 0, the DM's first packets errored
  // twice within a millisecond. The second try used to wait a full second.
  const lines: string[] = [];
  const h = harness(undefined, undefined, { logLines: lines });
  h.session.noteMember(ALICE, false);
  await speak(h, ALICE, 1);
  await fail(h, ALICE); // the first try: at once
  await fail(h, ALICE); // the next packet errors too: the second try waits briefly
  assert.deepEqual(h.receiver.subscribed, [ALICE, ALICE]);
  mock.timers.tick(100 - 21); // nextFrame moved the clock 20 ms already
  assert.deepEqual(h.receiver.subscribed, [ALICE, ALICE]); // not early
  mock.timers.tick(1);
  assert.deepEqual(h.receiver.subscribed, [ALICE, ALICE, ALICE]);
  await nextFrame();
  await speak(h, ALICE, 10); // their packets decrypt again
  await stop(h, ALICE);
  const [report] = health(h.sent);
  assert.ok(report);
  assert.equal(report.framesReceived, 11);
  // 140 ms lost (the 100 ms wait and the frames around it); a full second before.
  assert.equal(report.framesExpected - report.framesReceived, 7);
  const said = lines.join("\n");
  assert.match(said, /listening again at once \(1 of 5 this minute\)/);
  assert.match(said, /listening again in 100 ms \(2 of 5 this minute\)/);
});

test("each try waits its step, then ears gives up after about 4 s of failing", async () => {
  assert.equal(RESUBSCRIBE_DELAYS_MS.length, RESUBSCRIBE_LIMIT);
  const lines: string[] = [];
  const h = harness(undefined, undefined, { logLines: lines });
  h.session.noteMember(ALICE, false);
  await speak(h, ALICE, 1);
  const started = Date.now();
  for (const [i, delay] of RESUBSCRIBE_DELAYS_MS.entries()) {
    h.receiver.fail(ALICE); // every new stream fails on its first packet
    await new Promise((resolve) => setImmediate(resolve));
    if (delay > 0) {
      mock.timers.tick(delay - 1);
      assert.equal(h.receiver.subscribed.length, i + 1, `try ${i + 1} came early`);
      mock.timers.tick(1);
    }
    assert.equal(h.receiver.subscribed.length, i + 2, `try ${i + 1}`);
  }
  assert.equal(Date.now() - started, 4_000);
  h.receiver.fail(ALICE); // the sixth error: give up
  await nextFrame();
  assert.equal(h.receiver.subscribed.length, 6);
  assert.equal(warnings(h.sent).length, 1);
  const said = lines.join("\n");
  assert.match(said, /listening again in 300 ms \(3 of 5 this minute\)/);
  assert.match(said, /listening again in 2100 ms \(5 of 5 this minute\)/);
});

test("a packet heard starts the waits over; the minute's limit still counts", async () => {
  const lines: string[] = [];
  const h = harness(undefined, undefined, { logLines: lines });
  h.session.noteMember(ALICE, false);
  await speak(h, ALICE, 1);
  await fail(h, ALICE); // 1: at once
  await fail(h, ALICE); // 2: in 100 ms
  mock.timers.tick(100);
  await speak(h, ALICE, 3); // heard again
  assert.equal(h.receiver.subscribed.length, 3);
  await fail(h, ALICE); // a new burst: 3, at once
  assert.equal(h.receiver.subscribed.length, 4); // without waiting
  const said = lines.join("\n");
  assert.match(said, /listening again at once \(3 of 5 this minute\)/);
});

test("stopped between the error and the stream's close: no retry timer is set", async () => {
  const h = harness();
  h.session.noteMember(ALICE, false);
  await speak(h, ALICE, 1);
  await fail(h, ALICE); // 1: at once
  const stream = h.receiver.subscriptions.get(ALICE);
  assert.ok(stream);
  // After ears' own "error" listener (the 2nd try, 100 ms) and before the "close".
  stream.once("error", () => h.session.dropSpeakers([ALICE]));
  let timers = 0;
  const real = globalThis.setTimeout;
  globalThis.setTimeout = ((...args: Parameters<typeof setTimeout>) => {
    timers++;
    return real(...args);
  }) as typeof setTimeout;
  try {
    h.receiver.fail(ALICE);
    await new Promise((resolve) => setImmediate(resolve)); // the error, then the close
  } finally {
    globalThis.setTimeout = real;
  }
  assert.equal(timers, 0); // the close handler saw they had stopped
  mock.timers.tick(5_000);
  assert.deepEqual(h.receiver.subscribed, [ALICE, ALICE]);
});

test("five errors in a minute: ears stops listening to them and tells core, once", async () => {
  const h = harness();
  h.session.noteMember(ALICE, false);
  await speak(h, ALICE, 1);
  await fail(h, ALICE); // the first try is at once
  for (let i = 1; i < 5; i++) await failAndWait(h, ALICE, i); // the others after a wait
  assert.equal(h.receiver.subscribed.length, 6); // the first, and 5 tries
  await fail(h, ALICE); // the sixth error: give up
  assert.equal(h.receiver.subscribed.length, 6);
  const [report] = health(h.sent);
  assert.ok(report);
  assert.equal(report.framesReceived, 1);
  assert.ok(report.framesExpected > 4 * 50, `the waits count as lost: ${report.framesExpected}`);
  assert.deepEqual(warnings(h.sent), [
    { type: "status", state: "warning", guildId: GUILD, userId: ALICE, detail: "Their audio kept failing." },
  ]);
  // They're captured again when they next start speaking; failing again in the same
  // minute ends that at once, but core isn't told twice.
  await stop(h, ALICE);
  await speak(h, ALICE, 2);
  assert.equal(h.receiver.subscribed.length, 7);
  await fail(h, ALICE);
  assert.equal(warnings(h.sent).length, 1);
  assert.equal(h.receiver.subscribed.length, 7);
});

test("the tries come back after a minute", async () => {
  const h = harness();
  h.session.noteMember(ALICE, false);
  await speak(h, ALICE, 1);
  await fail(h, ALICE);
  for (let i = 1; i < 5; i++) await failAndWait(h, ALICE, i);
  mock.timers.tick(60_000);
  await fail(h, ALICE);
  assert.deepEqual(warnings(h.sent), []);
  assert.equal(h.receiver.subscribed.length, 7);
});

test("an error right after a pause doesn't count the pause as lost", async () => {
  const h = harness();
  h.session.noteMember(ALICE, false);
  await speak(h, ALICE, 3);
  for (let i = 0; i < 5; i++) {
    h.receiver.packet(ALICE, SILENCE); // the client's silence run before a pause
    await nextFrame();
  }
  mock.timers.tick(600); // the pause (the stream is still open)
  await fail(h, ALICE); // the first packet after it doesn't decrypt
  await stop(h, ALICE);
  // 8 heard, and one lost: not the 600 ms pause.
  assert.deepEqual(health(h.sent), [{ framesReceived: 8, framesExpected: 9 }]);
});

test("a normal pause isn't counted as lost by the watchdog", async () => {
  const h = harness();
  h.session.noteMember(ALICE, false);
  await speak(h, ALICE, 5);
  for (let i = 0; i < 5; i++) {
    h.receiver.packet(ALICE, SILENCE);
    await nextFrame();
  }
  h.receiver.quiet(ALICE); // they stop sending: the speaking map lets them go after 100 ms
  mock.timers.tick(700);
  await speak(h, ALICE, 5); // back before the stream ends
  await stop(h, ALICE);
  assert.deepEqual(health(h.sent), [{ framesReceived: 15, framesExpected: 15 }]);
});

test("packets arriving but none heard for 3 s: core is told too", async () => {
  const h = harness();
  h.session.noteMember(ALICE, false);
  h.receiver.sending(ALICE);
  for (let i = 0; i < 3; i++) mock.timers.tick(WATCHDOG_MS);
  assert.equal(warnings(h.sent).length, 1);
  for (let i = 0; i < 5; i++) mock.timers.tick(WATCHDOG_MS);
  assert.equal(warnings(h.sent).length, 1); // not again within the minute
});

test("another kind of receive error counts the time lost too", async () => {
  const h = harness();
  h.session.noteMember(ALICE, false);
  await speak(h, ALICE, 3);
  await fail(h, ALICE, "Failed to parse packet");
  await stop(h, ALICE);
  assert.deepEqual(health(h.sent), [{ framesReceived: 3, framesExpected: 4 }]);
});

test("loss is reported even when nothing got through (the DM screen said 100%)", async () => {
  const h = harness();
  h.session.noteMember(ALICE, false);
  h.receiver.sending(ALICE); // speaking, but no packet decrypts
  mock.timers.tick(740);
  await fail(h, ALICE);
  await stop(h, ALICE);
  assert.deepEqual(health(h.sent), [{ framesReceived: 0, framesExpected: 37 }]);
});

test("a stream that hears nothing after the error ends once they stop sending", async () => {
  const h = harness();
  h.session.noteMember(ALICE, false);
  await speak(h, ALICE, 2);
  await fail(h, ALICE);
  h.receiver.quiet(ALICE); // they stop; the new stream never gets a packet, so never ends
  mock.timers.tick(1_000);
  await nextFrame();
  assert.equal(ends(h.sent), 1); // core hears the speech ended
  assert.deepEqual(health(h.sent), [{ framesReceived: 2, framesExpected: 3 }]);
});

test("packets arriving but none heard count as lost, with no error at all", async () => {
  // Another speaker's good packets reset the library's failure count, so it never errors.
  const h = harness();
  h.session.noteMember(ALICE, false);
  h.receiver.sending(ALICE);
  mock.timers.tick(1_000);
  mock.timers.tick(1_000);
  h.receiver.quiet(ALICE);
  mock.timers.tick(1_000);
  await nextFrame();
  assert.deepEqual(health(h.sent), [{ framesReceived: 0, framesExpected: 100 }]);
  assert.equal(ends(h.sent), 1);
});

test("normal speech is never counted as lost by the watchdog", async () => {
  const h = harness();
  h.session.noteMember(ALICE, false);
  await speak(h, ALICE, 10);
  await stop(h, ALICE);
  mock.timers.tick(5_000);
  assert.deepEqual(health(h.sent), [{ framesReceived: 10, framesExpected: 10 }]);
});

test("consent withdrawn before ears listens again: no new subscription", async () => {
  const h = harness();
  h.session.noteMember(ALICE, false);
  await speak(h, ALICE, 2);
  h.receiver.fail(ALICE);
  h.allowlist.set(GUILD, [CAROL]); // not dropped yet: the check before re-listening catches it
  await nextFrame();
  assert.deepEqual(h.receiver.subscribed, [ALICE]);
  assert.deepEqual(health(h.sent), [{ framesReceived: 2, framesExpected: 3 }]);
});

test("a speaker dropped meanwhile isn't listened to again", async () => {
  const h = harness();
  h.session.noteMember(ALICE, false);
  await speak(h, ALICE, 2);
  h.receiver.fail(ALICE);
  h.session.dropSpeakers([ALICE]);
  await nextFrame();
  assert.deepEqual(h.receiver.subscribed, [ALICE]);
});

test("with DMBOT_DEBUG_AUDIO, the encryption's debug lines are logged, and only those", () => {
  const logLines: string[] = [];
  const debugListeners: ((message: string) => void)[] = [];
  harness(undefined, undefined, { debugAudio: true, logLines, debugListeners });
  assert.equal(debugListeners.length, 1);
  const emit = (message: string) => debugListeners[0]?.(message);
  emit("[NW] [DAVE] Transition executed (v0 -> v1, id: 0)");
  emit("[NW] [DAVE] Failed to decrypt a packet (1 consecutive fails)");
  emit("[NW] [DAVE] Failed to decrypt a packet (2 consecutive fails)"); // within a second
  emit("[NW] [DAVE] Failed to decrypt a packet (reinitializing session)");
  mock.timers.tick(1_000);
  emit("[NW] [DAVE] Failed to decrypt a packet (40 consecutive fails)");
  emit('[NW] [WS] >> {"op":0,"d":{"token":"secret-token"}}'); // never: it holds credentials
  emit('[NW] [WS] << {"d":"[NW] [DAVE] secret-token"}'); // not even with the tag inside
  const logged = logLines.join("\n");
  assert.match(logged, /dave: Transition executed \(v0 -> v1, id: 0\)/);
  assert.match(logged, /dave: Failed to decrypt a packet \(1 consecutive fails\)/);
  assert.match(logged, /dave: Failed to decrypt a packet \(40 consecutive fails\) \(and 2 more like it\)/);
  assert.doesNotMatch(logged, /\(2 consecutive/);
  assert.doesNotMatch(logged, /secret-token/);
});

test("without it, the connection's debug lines aren't even asked for", () => {
  const debugListeners: ((message: string) => void)[] = [];
  harness(undefined, undefined, { debugListeners });
  assert.equal(debugListeners.length, 0);
});

// ---- a stream the library ends while they're still sending (#645) -----------------

const ERIN = "1005"; // opted in

test("a stream ended while they're still sending is a failure, not the end of their speech", async () => {
  const h = harness();
  h.session.noteMember(ALICE, false);
  await speak(h, ALICE, 5);
  mock.timers.tick(780); // their packets stop decrypting, without an error
  h.receiver.endWhileSending(ALICE);
  await nextFrame();
  assert.deepEqual(h.receiver.subscribed, [ALICE, ALICE]); // listened to again at once
  assert.equal(ends(h.sent), 0);
  await speak(h, ALICE, 3);
  await stop(h, ALICE);
  // 5 heard, the 800 ms with nothing heard lost, 3 heard.
  assert.deepEqual(health(h.sent), [{ framesReceived: 8, framesExpected: 5 + 40 + 3 }]);
});

test("consent withdrawn during the wait before a later try: no new subscription", async () => {
  const h = harness();
  h.session.noteMember(ALICE, false);
  await speak(h, ALICE, 1);
  await fail(h, ALICE); // the first try, at once
  await fail(h, ALICE); // the second waits a little
  h.allowlist.set(GUILD, [CAROL]); // only the list changes: no dropSpeakers
  mock.timers.tick(RESUBSCRIBE_DELAYS_MS[1] ?? 0);
  await nextFrame();
  assert.deepEqual(h.receiver.subscribed, [ALICE, ALICE]);
  assert.equal(ends(h.sent), 1);
});

test("two speakers failing at once stay independent", async () => {
  const h = harness();
  h.allowlist.set(GUILD, [ALICE, ERIN]);
  h.session.noteMember(ALICE, false);
  h.session.noteMember(ERIN, false);
  h.receiver.packet(ALICE);
  h.receiver.packet(ERIN);
  await nextFrame();
  h.receiver.fail(ALICE);
  h.receiver.fail(ERIN);
  await nextFrame();
  assert.deepEqual(h.receiver.subscribed, [ALICE, ERIN, ALICE, ERIN]);
  for (let i = 0; i < 3; i++) {
    h.receiver.packet(ALICE);
    h.receiver.packet(ERIN);
    await nextFrame();
  }
  await stop(h, ALICE);
  await stop(h, ERIN);
  const reports = h.sent.flatMap((m) => (m.type === "health" ? [[m.userId, m.framesReceived]] : []));
  assert.deepEqual(reports, [
    [ALICE, 4],
    [ERIN, 4],
  ]);
  assert.equal(h.audio.length, 8); // both heard again
});

test("a client that sends silence frames through a pause still ends normally", async () => {
  const h = harness();
  h.session.noteMember(ALICE, false);
  await speak(h, ALICE, 5);
  for (let i = 0; i < 50; i++) {
    h.receiver.packet(ALICE, SILENCE); // a second of silence frames, every 20 ms
    await nextFrame();
  }
  h.receiver.endWhileSending(ALICE); // the library's 800 ms ran out: silence doesn't reset it
  await nextFrame();
  assert.deepEqual(h.receiver.subscribed, [ALICE]); // no re-listen
  assert.equal(ends(h.sent), 1);
  assert.deepEqual(warnings(h.sent), []);
});

test("ends while sending and receive errors share the five tries", async () => {
  const h = harness();
  h.session.noteMember(ALICE, false);
  await speak(h, ALICE, 1);
  await fail(h, ALICE); // 1: at once
  await failAndWait(h, ALICE, 1); // 2
  await failAndWait(h, ALICE, 2); // 3
  for (let i = 0; i < 2; i++) {
    // 4 and 5: the library ends the stream while they still send
    mock.timers.tick(200);
    h.receiver.endWhileSending(ALICE);
    await nextFrame();
    mock.timers.tick(RESUBSCRIBE_DELAYS_MS[3 + i] ?? 0);
    await nextFrame();
  }
  assert.equal(h.receiver.subscribed.length, 6);
  mock.timers.tick(200);
  h.receiver.endWhileSending(ALICE); // the sixth: give up
  await nextFrame();
  assert.equal(h.receiver.subscribed.length, 6);
  assert.equal(ends(h.sent), 1);
  assert.equal(warnings(h.sent).length, 1);
});

test("after re-listening once, a stream that hears nothing is left to the watchdog", async () => {
  const h = harness();
  h.session.noteMember(ALICE, false);
  await speak(h, ALICE, 5);
  mock.timers.tick(780);
  h.receiver.endWhileSending(ALICE);
  await nextFrame();
  mock.timers.tick(3_000); // still sending, nothing heard: lost, not re-listened again
  assert.deepEqual(h.receiver.subscribed, [ALICE, ALICE]);
  assert.equal(ends(h.sent), 0);
  h.receiver.quiet(ALICE);
  mock.timers.tick(1_000);
  await nextFrame();
  assert.equal(ends(h.sent), 1);
  const [report] = health(h.sent);
  assert.ok(report && report.framesExpected > report.framesReceived + 150);
});
