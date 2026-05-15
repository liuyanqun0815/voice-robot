import type { AudioChunkEvent, CancelEvent, TurnCommitRequestEvent, VadEvent } from "./events";

export class VoiceSocket {
  private readonly ws: WebSocket;
  private opened = false;

  constructor(url: string) {
    // 注意：本类只管理“单条”连接；自动重连策略由页面层处理。
    console.info("[voice/ws] connecting:", url);
    this.ws = new WebSocket(url);
    this.ws.addEventListener("open", () => {
      this.opened = true;
      console.info("[voice/ws] connected");
    });
    this.ws.addEventListener("close", () => {
      this.opened = false;
      console.info("[voice/ws] closed");
    });
  }

  waitUntilOpen(): Promise<void> {
    if (this.opened || this.ws.readyState === WebSocket.OPEN) {
      return Promise.resolve();
    }
    return new Promise((resolve, reject) => {
      // 首次建立连接时等待 open，若 error 则交由上层决定是否重试。
      const onOpen = () => {
        this.ws.removeEventListener("error", onError);
        resolve();
      };
      const onError = () => {
        this.ws.removeEventListener("open", onOpen);
        reject(new Error("WebSocket open failed"));
      };
      this.ws.addEventListener("open", onOpen, { once: true });
      this.ws.addEventListener("error", onError, { once: true });
    });
  }

  sendCommit(event: TurnCommitRequestEvent): void {
    if (!this.opened && this.ws.readyState !== WebSocket.OPEN) {
      console.warn("[voice/ws] skip commit, socket not open");
      return;
    }
    // commit 是“单轮提交开关”，应尽量避免重复发送。
    console.debug("[voice/ws] send commit", event.turn_id, event.reason);
    this.ws.send(JSON.stringify(event));
  }

  sendAudio(event: AudioChunkEvent): void {
    if (!this.opened && this.ws.readyState !== WebSocket.OPEN) {
      return;
    }
    // audio_chunk 发送频率高，默认不逐条打印日志，避免控制台刷屏。
    this.ws.send(JSON.stringify(event));
  }

  sendVadEvent(event: VadEvent): void {
    if (!this.opened && this.ws.readyState !== WebSocket.OPEN) {
      return;
    }
    console.debug("[voice/ws] send vad", event.event, event.turn_id);
    this.ws.send(JSON.stringify(event));
  }

  sendCancel(event: CancelEvent): void {
    if (!this.opened && this.ws.readyState !== WebSocket.OPEN) {
      console.warn("[voice/ws] skip cancel, socket not open");
      return;
    }
    console.info("[voice/ws] send cancel", event.turn_id, event.generation_id);
    this.ws.send(JSON.stringify(event));
  }

  onMessage(handler: (event: MessageEvent<string>) => void): void {
    this.ws.addEventListener("message", handler as EventListener);
  }

  onClose(handler: () => void): void {
    this.ws.addEventListener("close", handler as EventListener);
  }

  onError(handler: () => void): void {
    this.ws.addEventListener("error", handler as EventListener);
  }

  close(): void {
    this.ws.close();
  }
}
