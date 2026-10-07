import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import { Readable, Transform } from "node:stream";
import { afterEach, beforeEach, mock, test } from "node:test";
import { VoiceReceiver, type DiscordGatewayAdapterCreator, type VoiceConnection } from "@discordjs/voice";
import { Allowlist } from "../src/consent.js";
import { Logger } from "../src/log.js";
import type { EarsMessage } from "../src/protocol.js";
import { TableSession, UNKNOWN_RETRY_MS, type BotLookup } from "../src/voice.js";

const GUILD = "111";
const CHANNEL = "222";
const ALICE = "1001"; // opted in
const BOB = "1002"; // not opted in
const CAROL = "1003"; // on the list, but noted as a bot
const DAVE = "1004"; // never seen, not opted in
const SPEECH = Buffer.from([0x78, 0x01, 0x02]); // any non-silence Opus packet

/**
 * Behaves like @discordjs/voice's VoiceReceiver.onUdpMessage: each packet first tells
 * `speaking` (which emits "start" for a new speaker), then goes to the user's
 * subscription, or is dropped if there is none yet.
 */
class FakeReceiver {
  readonly speaking = new EventEmitter();
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
    if (!this.talking.has(userId)) {
      this.talking.add(userId);
      this.speaking.emit("start", userId);
    }
    this.subscriptions.get(userId)?.push(packet);
  }

  /** The speaker goes quiet; their stream ends as it would after the silence timeout. */
  stop(userId: string): void {
    this.talking.delete(userId);
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

function harness(
  lookUpBot: BotLookup = () => Promise.resolve(false),
  cached: ReadonlyMap<string, boolean> = new Map(),
): Harness {
  const receiver = new FakeReceiver();
  const sent: EarsMessage[] = [];
  const audio: Buffer[] = [];
  const lookups: string[] = [];
  const connection = { receiver, on: () => connection, state: { status: "ready" }, destroy: () => undefined };
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
    log: new Logger({ format: "text", level: "ERROR", shards: { count: 1, ids: [0] }, write: () => undefined }),
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

beforeEach(() => mock.timers.enable({ apis: ["Date"], now: 1_000_000 }));
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

test("the real receiver announces a new speaker before routing their packet", () => {
  // TableSession relies on this order to keep the first packet; guard it across upgrades.
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
