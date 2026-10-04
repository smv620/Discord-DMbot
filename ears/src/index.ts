import { Client, Events, GatewayIntentBits } from "discord.js";
import { loadConfig } from "./config.js";
import { CONFIRM_MS, SessionAudit } from "./audit.js";
import { Allowlist } from "./consent.js";
import { CoreLink } from "./coreLink.js";
import { Logger } from "./log.js";
import type { CoreCommand } from "./protocol.js";
import { TableSession } from "./voice.js";

/**
 * ears entry point. Logs in with the shared bot token using only the voice-state
 * intent, connects to core, and joins/leaves the table channel on core's command.
 * Slash commands and all game logic live in core.
 */
const config = loadConfig();
const log = new Logger({ format: config.logFormat, level: config.logLevel, shards: config.shards });
const allowlist = new Allowlist();
const sessions = new Map<string, TableSession>();
const audit = new SessionAudit();
let auditTimer: NodeJS.Timeout | null = null;
const link = new CoreLink({ url: config.coreUrl, secret: config.secret, shards: config.shards, log });

// The same shards as core (SHARD_COUNT / SHARD_IDS), so ears can join voice in every
// server core serves. core checks this on connect.
const client = new Client({
  intents: [GatewayIntentBits.Guilds, GatewayIntentBits.GuildVoiceStates],
  shards: config.shards.ids,
  shardCount: config.shards.count,
});

async function isBotOrUnknown(guildId: string, userId: string): Promise<boolean> {
  const guild = client.guilds.cache.get(guildId);
  if (!guild) return true;
  const member = guild.members.cache.get(userId) ?? (await guild.members.fetch(userId).catch(() => null));
  return member ? member.user.bot : true;
}

async function handleCommand(command: CoreCommand): Promise<void> {
  audit.confirm(command.guildId);
  switch (command.type) {
    case "allowlist": {
      const removed = allowlist.set(command.guildId, command.userIds);
      sessions.get(command.guildId)?.dropSpeakers(removed);
      return;
    }
    case "leave": {
      sessions.get(command.guildId)?.destroy();
      allowlist.clear(command.guildId);
      link.send({ type: "status", state: "left", guildId: command.guildId });
      return;
    }
    case "join": {
      const guild = client.guilds.cache.get(command.guildId);
      if (!guild) {
        link.send({ type: "status", state: "error", guildId: command.guildId, detail: "ears is not in that server." });
        return;
      }
      const existing = sessions.get(command.guildId);
      if (existing && existing.channelId === command.channelId) {
        // Already in that channel (core re-sent the join after reconnecting): keep the
        // connection instead of dropping and rejoining, and confirm it.
        try {
          await existing.ready();
          link.send({ type: "status", state: "joined", guildId: command.guildId, channelId: command.channelId });
          return;
        } catch {
          existing.destroy(); // not healthy after all: rejoin below
        }
      }
      sessions.get(command.guildId)?.destroy();
      const session = new TableSession({
        guildId: command.guildId,
        channelId: command.channelId,
        adapterCreator: guild.voiceAdapterCreator,
        allowlist,
        link,
        isBotOrUnknown: (userId) => isBotOrUnknown(command.guildId, userId),
        debugAudio: config.debugAudio,
        log,
        onClosed: () => {
          if (sessions.get(command.guildId) === session) sessions.delete(command.guildId);
        },
      });
      sessions.set(command.guildId, session);
      try {
        await session.ready();
        link.send({ type: "status", state: "joined", guildId: command.guildId, channelId: command.channelId });
      } catch {
        session.destroy();
        link.send({
          type: "status",
          state: "error",
          guildId: command.guildId,
          detail: "Could not connect to the voice channel within 20 seconds. Check the bot's Connect permission.",
        });
      }
      return;
    }
  }
}

link.on("command", (command) => {
  handleCommand(command).catch((err: unknown) => {
    log.error("command from core failed", { guildId: command.guildId, error: err });
  });
});

link.on("connected", () => {
  log.info("connected to core");
  link.send({ type: "status", state: "ready" });
  // Stop capturing at once in every session until core re-sends its consent list: a
  // player may have opted out while the link was down (see audit.ts). core sends the
  // list again straight away for every session it still wants.
  for (const [guildId, session] of sessions) {
    session.dropSpeakers(allowlist.set(guildId, []));
  }
  // Leave any voice session core doesn't ask for again soon.
  audit.begin(sessions.keys());
  if (auditTimer) clearTimeout(auditTimer);
  auditTimer = setTimeout(() => {
    for (const guildId of audit.takeUnconfirmed()) {
      if (!sessions.has(guildId)) continue;
      log.info("leaving voice: core didn't ask for this session after reconnecting", { guildId });
      sessions.get(guildId)?.destroy();
      allowlist.clear(guildId);
      link.send({ type: "status", state: "left", guildId });
    }
  }, CONFIRM_MS);
});

link.on("disconnected", () => {
  log.warn("core link down; retrying");
});

// Connect to core only after Discord is ready, so join commands can find the server.
client.once(Events.ClientReady, (ready) => {
  log.info(
    `logged in as ${ready.user.tag}, shards ${config.shards.ids.join(",")} of ${config.shards.count}`,
  );
  link.start();
});

function shutdown(): void {
  if (auditTimer) clearTimeout(auditTimer);
  for (const session of sessions.values()) session.destroy();
  link.stop();
  void client.destroy().finally(() => process.exit(0));
}
process.on("SIGINT", shutdown);
process.on("SIGTERM", shutdown);

await client.login(config.discordToken);
