import { parseLogFormat, parseLogLevel, type LogLevel } from "./log.js";
import { parseShards, type ShardSettings } from "./shards.js";

export interface EarsConfig {
  discordToken: string;
  coreUrl: string;
  secret: string;
  /** DMBOT_DEBUG_AUDIO=1: log per-utterance audio health (user IDs and counts only). */
  debugAudio: boolean;
  shards: ShardSettings;
  logFormat: "text" | "json";
  logLevel: LogLevel;
}

/** Read configuration from environment variables. Throws a readable error if anything is missing. */
export function loadConfig(env: NodeJS.ProcessEnv = process.env): EarsConfig {
  const missing: string[] = [];
  const need = (name: string): string => {
    const value = env[name]?.trim();
    if (!value) missing.push(name);
    return value ?? "";
  };

  const discordToken = need("DISCORD_TOKEN");
  const secret = need("EARS_SHARED_SECRET");
  const host = env.EARS_WS_HOST?.trim() || "127.0.0.1";
  const port = env.EARS_WS_PORT?.trim() || "8765";

  if (missing.length > 0) {
    throw new Error(`Missing required settings: ${missing.join(", ")}. Copy .env.example to .env and fill them in.`);
  }
  if (!/^\d+$/.test(port)) {
    throw new Error(`EARS_WS_PORT must be a number, got "${port}".`);
  }
  return {
    discordToken,
    secret,
    coreUrl: `ws://${host}:${port}`,
    debugAudio: env.DMBOT_DEBUG_AUDIO?.trim() === "1",
    shards: parseShards(env.SHARD_COUNT, env.SHARD_IDS),
    logFormat: parseLogFormat(env.LOG_FORMAT),
    logLevel: parseLogLevel(env.LOG_LEVEL),
  };
}
