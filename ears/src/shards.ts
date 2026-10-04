/**
 * Shard settings: which part of Discord this process serves. Same rules as
 * core/src/dmbot/sharding.py — core refuses an ears whose settings differ.
 *
 * Server g belongs to shard (g >> 22) % shardCount.
 */
export const SHARD_COUNT_MAX = 4096;

export interface ShardSettings {
  count: number;
  ids: number[];
}

export function shardFor(guildId: string, shardCount: number): number {
  return Number((BigInt(guildId) >> 22n) % BigInt(shardCount));
}

/** Read SHARD_COUNT (default 1) and SHARD_IDS (default: all shards, e.g. "0,1"). */
export function parseShards(countRaw: string | undefined, idsRaw: string | undefined): ShardSettings {
  const countText = countRaw?.trim() || "1";
  if (!/^\d+$/.test(countText)) {
    throw new Error(`SHARD_COUNT must be a whole number, got "${countText}".`);
  }
  const count = Number(countText);
  if (count < 1 || count > SHARD_COUNT_MAX) {
    throw new Error(`SHARD_COUNT must be between 1 and ${SHARD_COUNT_MAX}.`);
  }

  const idsText = idsRaw?.trim() ?? "";
  if (!idsText) return { count, ids: Array.from({ length: count }, (_, i) => i) };
  const ids: number[] = [];
  for (const raw of idsText.split(",")) {
    const part = raw.trim();
    if (!/^\d+$/.test(part)) {
      throw new Error(`SHARD_IDS must be shard numbers separated by commas, like "0,1"; got "${idsText}".`);
    }
    const shard = Number(part);
    if (shard >= count) {
      throw new Error(
        `SHARD_IDS has shard ${shard}, but SHARD_COUNT is ${count} (shards are numbered 0 to ${count - 1}).`,
      );
    }
    if (ids.includes(shard)) throw new Error(`SHARD_IDS lists shard ${shard} twice.`);
    ids.push(shard);
  }
  return { count, ids: ids.sort((a, b) => a - b) };
}
