export interface WSEvent {
  type: "job_update" | "log" | "gpu_status" | "project_update" | "clip_update" | "pong";
  timestamp?: string;
  data?: Record<string, unknown>;
}

type EventHandler = (event: WSEvent) => void;

class WSClient {
  private ws: WebSocket | null = null;
  private url = "";
  private closedManually = false;
  private reconnectTimer: number | null = null;
  private pingTimer: number | null = null;
  private handlers = new Set<EventHandler>();
  private statusListeners = new Set<(connected: boolean) => void>();

  connect(wsUrl: string): void {
    this.url = wsUrl;
    this.closedManually = false;
    this.openSocket();
  }

  disconnect(): void {
    this.closedManually = true;
    this.clearTimers();
    if (this.ws) {
      this.ws.close();
      this.ws = null;
    }
    this.notifyStatus(false);
  }

  subscribe(handler: EventHandler): () => void {
    this.handlers.add(handler);
    return () => {
      this.handlers.delete(handler);
    };
  }

  onStatus(listener: (connected: boolean) => void): () => void {
    this.statusListeners.add(listener);
    return () => {
      this.statusListeners.delete(listener);
    };
  }

  private notifyStatus(connected: boolean): void {
    for (const listener of this.statusListeners) {
      listener(connected);
    }
  }

  private clearTimers(): void {
    if (this.reconnectTimer !== null) {
      window.clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    if (this.pingTimer !== null) {
      window.clearInterval(this.pingTimer);
      this.pingTimer = null;
    }
  }

  private openSocket(): void {
    if (!this.url) return;
    if (this.ws && (this.ws.readyState === WebSocket.OPEN || this.ws.readyState === WebSocket.CONNECTING)) {
      return;
    }

    try {
      const socket = new WebSocket(this.url);
      this.ws = socket;

      socket.onopen = () => {
        this.notifyStatus(true);
        this.pingTimer = window.setInterval(() => {
          if (socket.readyState === WebSocket.OPEN) {
            socket.send("ping");
          }
        }, 15000);
      };

      socket.onmessage = (msg) => {
        try {
          const parsed = JSON.parse(String(msg.data)) as WSEvent;
          for (const handler of this.handlers) {
            handler(parsed);
          }
        } catch {
          // ignore malformed event
        }
      };

      socket.onclose = () => {
        this.notifyStatus(false);
        this.clearTimers();
        this.ws = null;
        if (!this.closedManually) {
          this.reconnectTimer = window.setTimeout(() => this.openSocket(), 2000);
        }
      };

      socket.onerror = () => {
        socket.close();
      };
    } catch {
      this.notifyStatus(false);
      if (!this.closedManually) {
        this.reconnectTimer = window.setTimeout(() => this.openSocket(), 2500);
      }
    }
  }
}

export const wsClient = new WSClient();
