import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import { PassThrough, Readable } from "node:stream";
import { afterEach, beforeEach, mock, test } from "node:test";
import type { DiscordGatewayAdapterCreator, VoiceConnection } from "@discordjs/voice";
import { Allowlist } from "../src/consent.js";
import { Logger } from "../src/log.js";
import type { EarsMessage } from "../src/protocol.js";
import { TableSession, type BotLookup } from "../src/voice.js";

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
  lookups: string[];
}

function harness(lookUpBot: BotLookup = () => Promise.resolve(false)): Harness {
  const receiver = new FakeReceiver();
  const sent: EarsMessage[] = [];
  const lookups: string[] = [];
  const connection = { receiver, on: () => connection, state: { status: "ready" }, destroy: () => undefined };
  const allowlist = new Allowlist();
  allowlist.set(GUILD, [ALICE, CAROL]);
  const session = new TableSession({
    guildId: GUILD,
    channelId: CHANNEL,
    adapterCreator: (() => ({})) as unknown as DiscordGatewayAdapterCreator,
    allowlist,
    link: { send: (m) => sent.push(m), sendAudio: () => undefined, droppedAudioFrames: 0 },
    lookUpBot: (userId) => {
      lookups.push(userId);
      return lookUpBot(userId);
    },
    log: new Logger({ format: "text", level: "ERROR", shards: { count: 1, ids: [0] }, write: () => undefined }),
    // The fake has just the parts TableSession uses.
    connect: () => connection as unknown as VoiceConnection,
    createDecoder: () => new PassThrough(),
  });
  return { session, allowlist, receiver, sent, lookups };
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
  assert.deepEqual(h.lookups, []); // the noted member was used, not a lookup
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

test("someone who can't be found is treated as a bot", async () => {
  const h = harness(() => Promise.resolve(undefined));
  await speak(h, ALICE, 3);
  assert.deepEqual(h.receiver.subscribed, []);
  await stop(h, ALICE);
  await speak(h, ALICE, 3);
  assert.deepEqual(h.lookups, [ALICE, ALICE]); // not remembered: asked again
  assert.deepEqual(h.receiver.subscribed, []);
});

test("consent withdrawn during a lookup stops the capture", async () => {
  let answer: (isBot: boolean) => void = () => undefined;
  const h = harness(() => new Promise((resolve) => (answer = resolve)));
  await speak(h, ALICE, 1);
  h.allowlist.set(GUILD, [CAROL]); // opts out while Discord is asked
  answer(false);
  await nextFrame();
  await speak(h, ALICE, 3);
  assert.deepEqual(h.receiver.subscribed, []);
});

test("nothing is noted or captured after the session ends", async () => {
  const h = harness();
  h.session.destroy();
  h.session.noteMember(ALICE, false);
  await speak(h, ALICE, 3);
  assert.deepEqual(h.receiver.subscribed, []);
});
