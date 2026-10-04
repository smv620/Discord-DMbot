import { Client, Events, GatewayIntentBits } from "discord.js";
import { loadConfig } from "./config.js";
import { Allowlist } from "./consent.js";
import { CoreLink } from "./coreLink.js";
import type { CoreCommand } from "./protocol.js";
import { TableSession } from "./voice.js";

/**
 * ears entry point. Logs in with the shared bot token using only the voice-state
 * intent, connects to core, and joins/leaves the table channel on core's command.
 * Slash commands and all game logic live in core.
 */
const config = loadConfig();
const allowlist = new Allowlist();
const sessions = new Map<string, TableSession>();
const link = new CoreLink({ url: config.coreUrl, secret: config.secret });

const client = new Client({
  intents: [GatewayIntentBits.Guilds, GatewayIntentBits.GuildVoiceStates],
});

async function isBotOrUnknown(guildId: string, userId: string): Promise<boolean> {
  const guild = client.guilds.cache.get(guildId);
  if (!guild) return true;
  const member = guild.members.cache.get(userId) ?? (await guild.members.fetch(userId).catch(() => null));
  return member ? member.user.bot : true;
}

async function handleCommand(command: CoreCommand): Promise<void> {
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
      sessions.get(command.guildId)?.destroy();
      const session = new TableSession({
        guildId: command.guildId,
        channelId: command.channelId,
        adapterCreator: guild.voiceAdapterCreator,
        allowlist,
        link,
        isBotOrUnknown: (userId) => isBotOrUnknown(command.guildId, userId),
        debugAudio: config.debugAudio,
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
    console.error("[ears] command failed", err);
  });
});

link.on("connected", () => {
  console.log("[ears] connected to core");
  link.send({ type: "status", state: "ready" });
});

link.on("disconnected", () => {
  console.warn("[ears] core link down; retrying");
});

// Connect to core only after Discord is ready, so join commands can find the server.
client.once(Events.ClientReady, (ready) => {
  console.log(`[ears] logged in as ${ready.user.tag}`);
  link.start();
});

function shutdown(): void {
  for (const session of sessions.values()) session.destroy();
  link.stop();
  void client.destroy().finally(() => process.exit(0));
}
process.on("SIGINT", shutdown);
process.on("SIGTERM", shutdown);

await client.login(config.discordToken);
