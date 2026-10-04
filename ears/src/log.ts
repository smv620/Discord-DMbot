/**
 * Logging: plain text for local runs, one JSON object per line for servers
 * (LOG_FORMAT=json), matching core's fields: ts, level, logger, msg, shard_id,
 * guild_id. IDs only — never player names, audio, tokens, or keys.
 */
import { shardFor, type ShardSettings } from "./shards.js";

export type LogLevel = "DEBUG" | "INFO" | "WARNING" | "ERROR";
const ORDER: Record<LogLevel, number> = { DEBUG: 10, INFO: 20, WARNING: 30, ERROR: 40 };

export interface LogFields {
  guildId?: string;
  error?: unknown;
}

export interface LoggerOptions {
  format: "text" | "json";
  level: LogLevel;
  shards: ShardSettings;
  /** Where lines go; tests replace it. */
  write?: (line: string) => void;
}

export class Logger {
  constructor(private readonly options: LoggerOptions) {}

  debug(msg: string, fields: LogFields = {}): void {
    this.log("DEBUG", msg, fields);
  }
  info(msg: string, fields: LogFields = {}): void {
    this.log("INFO", msg, fields);
  }
  warn(msg: string, fields: LogFields = {}): void {
    this.log("WARNING", msg, fields);
  }
  error(msg: string, fields: LogFields = {}): void {
    this.log("ERROR", msg, fields);
  }

  private log(level: LogLevel, msg: string, fields: LogFields): void {
    if (ORDER[level] < ORDER[this.options.level]) return;
    const { shards } = this.options;
    const shardId =
      fields.guildId !== undefined
        ? shardFor(fields.guildId, shards.count)
        : shards.ids.length === 1
          ? shards.ids[0]
          : undefined;
    const err = fields.error === undefined ? undefined : describeError(fields.error);
    const write = this.options.write ?? ((line: string) => process.stderr.write(`${line}\n`));

    if (this.options.format === "json") {
      const entry: Record<string, unknown> = {
        ts: new Date().toISOString(),
        level,
        logger: "ears",
        msg,
      };
      if (shardId !== undefined) entry.shard_id = shardId;
      if (fields.guildId !== undefined) entry.guild_id = fields.guildId;
      if (err !== undefined) entry.exc = err;
      write(JSON.stringify(entry));
      return;
    }
    const ctx = [
      shardId !== undefined ? `shard=${shardId}` : "",
      fields.guildId !== undefined ? `guild=${fields.guildId}` : "",
    ]
      .filter(Boolean)
      .join(" ");
    write(`${new Date().toISOString()} ${level} ears${ctx ? ` [${ctx}]` : ""}: ${msg}${err ? `\n${err}` : ""}`);
  }
}

function describeError(error: unknown): string {
  if (error instanceof Error) return error.stack ?? `${error.name}: ${error.message}`;
  return String(error);
}

export function parseLogFormat(raw: string | undefined): "text" | "json" {
  const value = (raw?.trim() || "text").toLowerCase();
  if (value !== "text" && value !== "json") {
    throw new Error(`LOG_FORMAT must be "text" or "json", got "${value}".`);
  }
  return value;
}

export function parseLogLevel(raw: string | undefined): LogLevel {
  const value = (raw?.trim() || "INFO").toUpperCase();
  if (!(value in ORDER)) throw new Error(`LOG_LEVEL must be one of ${Object.keys(ORDER).join(", ")}.`);
  return value as LogLevel;
}
