import { EventEmitter } from "node:events";
import WebSocket from "ws";
import { parseCoreCommand, PROTOCOL_VERSION, type CoreCommand, type EarsMessage } from "./protocol.js";

/** Stop queueing audio if this much is waiting to be sent; core is not keeping up. */
const MAX_BUFFERED_BYTES = 2 * 1024 * 1024;
const MIN_BACKOFF_MS = 500;
const MAX_BACKOFF_MS = 15_000;

export interface CoreLinkOptions {
  url: string;
  secret: string;
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
  private backoffMs = MIN_BACKOFF_MS;
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

  sendAudio(frame: Buffer): void {
    const ws = this.ws;
    if (!ws || ws.readyState !== WebSocket.OPEN || ws.bufferedAmount > MAX_BUFFERED_BYTES) {
      this.droppedAudioFrames++;
      return;
    }
    ws.send(frame, { binary: true });
  }

  private connect(): void {
    const ws = new WebSocket(this.options.url);
    this.ws = ws;

    ws.on("open", () => {
      this.backoffMs = MIN_BACKOFF_MS;
      ws.send(JSON.stringify({ type: "hello", version: PROTOCOL_VERSION, secret: this.options.secret }));
      this.emit("connected");
    });

    ws.on("message", (data, isBinary) => {
      if (isBinary) return; // core never sends binary
      const command = parseCoreCommand(data.toString());
      if (command) {
        this.emit("command", command);
      } else {
        console.warn("[ears] ignored invalid message from core");
      }
    });

    ws.on("close", () => {
      if (this.ws === ws) this.ws = null;
      this.emit("disconnected");
      this.scheduleReconnect();
    });

    ws.on("error", (err) => {
      console.warn(`[ears] core link error: ${err.message}`);
    });
  }

  private scheduleReconnect(): void {
    if (this.stopped) return;
    const delay = this.backoffMs;
    this.backoffMs = Math.min(this.backoffMs * 2, MAX_BACKOFF_MS);
    this.reconnectTimer = setTimeout(() => this.connect(), delay);
  }
}
