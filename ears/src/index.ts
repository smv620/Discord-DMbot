import { Client, Events, GatewayIntentBits } from "discord.js";
import { loadConfig } from "./config.js";
import { CONFIRM_MS, SessionAudit } from "./audit.js";
import { Allowlist } from "./consent.js";
import { CoreLink } from "./coreLink.js";
import { Logger } from "./log.js";
import type { CoreCommand } from "./protocol.js";
import { READY_TIMEOUT_MS, TableSession } from "./voice.js";
import { noteVoiceMembers, type VoiceMember } from "./voiceMembers.js";

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

/** Whether a cached member is a bot, or undefined if discord.js hasn't cached them. */
function peekBot(guildId: string, userId: string): boolean | undefined {
  return client.guilds.cache.get(guildId)?.members.cache.get(userId)?.user.bot;
}

/** Whether a user is a bot, or undefined if they can't be found (treated as a bot). */
async function lookUpBot(guildId: string, userId: string): Promise<boolean | undefined> {
  const guild = client.guilds.cache.get(guildId);
  if (!guild) return undefined;
  const member = guild.members.cache.get(userId) ?? (await guild.members.fetch(userId).catch(() => null));
  return member?.user.bot;
}

/** Note who's a bot before they speak, so capture starts on their first packet. */
function noteMembers(guildId: string, session: TableSession, states: Iterable<VoiceMember>): void {
  noteVoiceMembers(
    session,
    states,
    (userId) => allowlist.isAllowed(guildId, userId, false),
    (userId) => lookUpBot(guildId, userId),
  );
}

async function handleCommand(command: CoreCommand): Promise<void> {
  audit.confirm(command.guildId);
  switch (command.type) {
    case "allowlist": {
      const removed = allowlist.set(command.guildId, command.userIds);
      log.info(`consent list: ${command.userIds.length} opted in`, { guildId: command.guildId });
      const session = sessions.get(command.guildId);
      session?.dropSpeakers(removed);
      // Someone who just opted in may not be cached: look them up now, not when they speak.
      const voiceStates = client.guilds.cache.get(command.guildId)?.voiceStates.cache.values();
      if (session && voiceStates) noteMembers(command.guildId, session, voiceStates);
      return;
    }
    case "leave": {
      if (sessions.has(command.guildId)) log.info("left voice (core asked)", { guildId: command.guildId });
      sessions.get(command.guildId)?.destroy();
      allowlist.clear(command.guildId);
      link.send({ type: "status", state: "left", guildId: command.guildId });
      return;
    }
    case "join": {
      const guild = client.guilds.cache.get(command.guildId);
      if (!guild) {
        log.warn("can't join voice: ears is not in that server", { guildId: command.guildId });
        link.send({ type: "status", state: "error", guildId: command.guildId, detail: "ears is not in that server." });
        return;
      }
      const existing = sessions.get(command.guildId);
      if (existing && existing.channelId === command.channelId) {
        // Already in that channel (core re-sent the join after reconnecting): keep the
        // connection instead of dropping and rejoining, and confirm it.
        try {
          await existing.ready();
          log.info(`still in voice channel ${command.channelId}; kept the connection`, { guildId: command.guildId });
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
        peekBot: (userId) => peekBot(command.guildId, userId),
        lookUpBot: (userId) => lookUpBot(command.guildId, userId),
        debugAudio: config.debugAudio,
        log,
        onClosed: () => {
          if (sessions.get(command.guildId) === session) sessions.delete(command.guildId);
        },
      });
      sessions.set(command.guildId, session);
      noteMembers(command.guildId, session, guild.voiceStates.cache.values());
      try {
        await session.ready();
        // ready() includes the DAVE (end-to-end encryption) handshake.
        log.info(`joined voice channel ${command.channelId} (encrypted connection ready)`, {
          guildId: command.guildId,
        });
        link.send({ type: "status", state: "joined", guildId: command.guildId, channelId: command.channelId });
      } catch {
        log.warn(
          `couldn't join voice channel ${command.channelId} within ${READY_TIMEOUT_MS / 1000} s ` +
            "(Connect permission, or the encryption handshake failed)",
          { guildId: command.guildId },
        );
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
    session.dropSpeakers(allowlist.set(guildId, []), "paused until core sends the consent list again");
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

// Someone joins the table channel: note whether they're a bot before they speak.
client.on(Events.VoiceStateUpdate, (_oldState, newState) => {
  const session = sessions.get(newState.guild.id);
  if (session) noteMembers(newState.guild.id, session, [newState]);
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
