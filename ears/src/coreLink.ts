import { EventEmitter } from "node:events";
import WebSocket from "ws";
import type { Logger } from "./log.js";
import { helloMessage, parseCoreCommand, type CoreCommand, type EarsMessage } from "./protocol.js";
import { ReconnectPolicy } from "./reconnect.js";
import type { ShardSettings } from "./shards.js";

/** Stop queueing audio if this much is waiting to be sent; core is not keeping up. */
const MAX_BUFFERED_BYTES = 2 * 1024 * 1024;

export interface CoreLinkOptions {
  url: string;
  secret: string;
  shards: ShardSettings;
  log: Logger;
}

export interface CoreLinkEvents {
  command: [CoreCommand];
  connected: [];
  disconnected: [];
}

/**
 * WebSocket client to core. Reconnects with exponential backoff and authenticates
 * with the shared secret on every connection. Audio sent while disconnected or
 * backed up is dropped and counted, never queued without bound.
 */
export class CoreLink extends EventEmitter<CoreLinkEvents> {
  private ws: WebSocket | null = null;
  private readonly policy = new ReconnectPolicy();
  private lastRejection: string | null = null;
  private stopped = false;
  private reconnectTimer: NodeJS.Timeout | null = null;
  droppedAudioFrames = 0;

  constructor(private readonly options: CoreLinkOptions) {
    super();
  }

  get isConnected(): boolean {
    return this.ws?.readyState === WebSocket.OPEN;
  }

  start(): void {
    this.stopped = false;
    this.connect();
  }

  stop(): void {
    this.stopped = true;
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
    this.ws?.close();
  }

  send(message: EarsMessage): void {
    if (this.isConnected) this.ws?.send(JSON.stringify(message));
  }

  /** Send one audio frame; false if it was dropped (no link, or too much waiting, #45). */
  sendAudio(frame: Buffer): boolean {
    const ws = this.ws;
    if (!ws || ws.readyState !== WebSocket.OPEN || ws.bufferedAmount > MAX_BUFFERED_BYTES) {
      this.droppedAudioFrames++;
      return false;
    }
    ws.send(frame, { binary: true });
    return true;
  }

  private connect(): void {
    const ws = new WebSocket(this.options.url);
    this.ws = ws;

    ws.on("open", () => {
      this.policy.opened(Date.now());
      ws.send(JSON.stringify(helloMessage(this.options.secret, this.options.shards)));
      this.emit("connected");
    });

    ws.on("message", (data, isBinary) => {
      if (isBinary) return; // core never sends binary
      const command = parseCoreCommand(data.toString());
      if (command) {
        this.emit("command", command);
      } else {
        this.options.log.warn("ignored an invalid message from core");
      }
    });

    ws.on("close", (code, reasonBuffer) => {
      if (this.ws === ws) this.ws = null;
      const { delayMs, rejected } = this.policy.closed(Date.now(), code);
      if (rejected) {
        // Log a refusal once per reason, not on every retry.
        const reason = reasonBuffer.toString() || "no reason given";
        if (reason !== this.lastRejection) {
          this.options.log.error(
            `core refused this ears: ${reason}. Retrying every ${delayMs / 1000} s until it's fixed.`,
          );
          this.lastRejection = reason;
        }
      } else {
        this.lastRejection = null;
        this.emit("disconnected");
      }
      this.scheduleReconnect(delayMs);
    });

    ws.on("error", (err) => {
      this.options.log.warn(`core link error: ${err.message}`);
    });
  }

  private scheduleReconnect(delayMs: number): void {
    if (this.stopped) return;
    this.reconnectTimer = setTimeout(() => this.connect(), delayMs);
  }
}
