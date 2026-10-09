import type { ShardSettings } from "./shards.js";

/**
 * ears <-> core wire protocol (version 2).
 *
 * Keep in sync with core/src/dmbot/ears/protocol.py. Shared test vectors live in
 * protocol/fixtures.json and are checked by both test suites.
 *
 * Transport: one WebSocket. ears is the client; core is the server.
 * - Text frames carry JSON control messages (both directions).
 * - Binary frames carry audio (ears -> core only):
 *     byte 0       kind (1 = PCM audio)
 *     bytes 1-8    Discord guild (server) ID, uint64 big-endian
 *     bytes 9-16   Discord user ID, uint64 big-endian
 *     bytes 17-24  capture timestamp, Unix epoch milliseconds, uint64 big-endian
 *     bytes 25-    PCM, signed 16-bit little-endian, mono, 16 kHz
 */

/** 2: hello carries the shard settings, so core can refuse an ears serving other shards. */
export const PROTOCOL_VERSION = 2;
export const AUDIO_FRAME_KIND = 1;
export const AUDIO_HEADER_BYTES = 25;
export const SAMPLE_RATE = 16_000;

// ---- ears -> core -------------------------------------------------------

export interface HelloMessage {
  type: "hello";
  version: number;
  secret: string;
  shardCount: number;
  shardIds: number[];
}

/**
 * "warning": a problem with one speaker's audio that the session survives: ears gave up
 * re-listening after repeated failures (#631, #645), or they keep sending but none of it
 * can be heard. Carries their userId. The others are about the connection.
 */
export type EarsState = "ready" | "joined" | "left" | "error" | "warning";

interface StatusFields {
  type: "status";
  guildId?: string;
  channelId?: string;
  detail?: string;
}

/** A warning is always about someone; the other states never are (core checks both). */
export type StatusMessage =
  | (StatusFields & { state: "warning"; userId: string })
  | (StatusFields & { state: Exclude<EarsState, "warning">; userId?: undefined });

export interface SpeakingMessage {
  type: "speaking";
  guildId: string;
  userId: string;
  event: "start" | "end";
  timestampMs: number;
}

/**
 * Per-speaker audio health for one utterance: how many 20 ms frames arrived vs expected.
 * Frames that didn't make it are already left out of `framesReceived`; the three counts
 * below say why, for the log (#43, #45). Each is omitted when zero.
 */
export interface HealthMessage {
  type: "health";
  guildId: string;
  userId: string;
  framesReceived: number;
  framesExpected: number;
  /** Packets the voice library couldn't decrypt (DAVE), counted as lost. */
  decryptFailures?: number;
  /** Packets the Opus decoder refused. */
  decodeErrors?: number;
  /** Frames dropped on the link to core because it was too busy. */
  linkDropped?: number;
}

export type EarsMessage = HelloMessage | StatusMessage | SpeakingMessage | HealthMessage;

// ---- core -> ears -------------------------------------------------------

export interface JoinCommand {
  type: "join";
  guildId: string;
  channelId: string;
}

export interface LeaveCommand {
  type: "leave";
  guildId: string;
}

/** The complete set of users who may be captured in a guild. Replaces any previous list. */
export interface AllowlistCommand {
  type: "allowlist";
  guildId: string;
  userIds: string[];
}

export type CoreCommand = JoinCommand | LeaveCommand | AllowlistCommand;

const SNOWFLAKE = /^\d{1,20}$/;

function isSnowflake(value: unknown): value is string {
  return typeof value === "string" && SNOWFLAKE.test(value);
}

/** Parse and validate a JSON control message from core. Returns null if invalid. */
export function parseCoreCommand(raw: string): CoreCommand | null {
  let data: unknown;
  try {
    data = JSON.parse(raw);
  } catch {
    return null;
  }
  if (typeof data !== "object" || data === null) return null;
  const msg = data as Record<string, unknown>;

  switch (msg.type) {
    case "join":
      if (isSnowflake(msg.guildId) && isSnowflake(msg.channelId)) {
        return { type: "join", guildId: msg.guildId, channelId: msg.channelId };
      }
      return null;
    case "leave":
      return isSnowflake(msg.guildId) ? { type: "leave", guildId: msg.guildId } : null;
    case "allowlist":
      if (
        isSnowflake(msg.guildId) &&
        Array.isArray(msg.userIds) &&
        msg.userIds.every(isSnowflake)
      ) {
        return { type: "allowlist", guildId: msg.guildId, userIds: [...msg.userIds] };
      }
      return null;
    default:
      return null;
  }
}

/** The first message on every connection: proves the shared secret and states the shards. */
export function helloMessage(secret: string, shards: ShardSettings): HelloMessage {
  return { type: "hello", version: PROTOCOL_VERSION, secret, shardCount: shards.count, shardIds: [...shards.ids] };
}

/** Build a binary audio frame. `pcm` must be s16le mono 16 kHz. */
export function encodeAudioFrame(
  guildId: string,
  userId: string,
  timestampMs: number,
  pcm: Buffer,
): Buffer {
  const frame = Buffer.allocUnsafe(AUDIO_HEADER_BYTES + pcm.length);
  frame.writeUInt8(AUDIO_FRAME_KIND, 0);
  frame.writeBigUInt64BE(BigInt(guildId), 1);
  frame.writeBigUInt64BE(BigInt(userId), 9);
  frame.writeBigUInt64BE(BigInt(Math.trunc(timestampMs)), 17);
  pcm.copy(frame, AUDIO_HEADER_BYTES);
  return frame;
}

export interface DecodedAudioFrame {
  guildId: string;
  userId: string;
  timestampMs: number;
  pcm: Buffer;
}

/** Decode a binary audio frame (used in tests and for symmetry with core). */
export function decodeAudioFrame(frame: Buffer): DecodedAudioFrame | null {
  if (frame.length < AUDIO_HEADER_BYTES || frame.readUInt8(0) !== AUDIO_FRAME_KIND) return null;
  const pcm = frame.subarray(AUDIO_HEADER_BYTES);
  if (pcm.length % 2 !== 0) return null;
  return {
    guildId: frame.readBigUInt64BE(1).toString(),
    userId: frame.readBigUInt64BE(9).toString(),
    timestampMs: Number(frame.readBigUInt64BE(17)),
    pcm,
  };
}
