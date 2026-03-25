type Callback = (data: unknown) => void;

export class QBitWebSocket {
  private ws: WebSocket | null = null;
  private url: string = "";
  private reconnectDelay = 1000;
  private maxDelay = 30000;
  private listeners: Map<string, Set<Callback>> = new Map();
  private shouldReconnect = false;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;

  connect(url: string) {
    this.url = url;
    this.shouldReconnect = true;
    this.reconnectDelay = 1000;
    this.doConnect();
  }

  private doConnect() {
    if (this.ws) {
      this.ws.onclose = null;
      this.ws.close();
    }

    try {
      this.ws = new WebSocket(this.url);
    } catch {
      this.scheduleReconnect();
      return;
    }

    this.ws.onopen = () => {
      this.reconnectDelay = 1000;
      this.emit("connection", { status: "connected" });
    };

    this.ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data);
        const channel = msg.channel || msg.type || "message";
        this.emit(channel, msg.data ?? msg);
      } catch {
        this.emit("message", event.data);
      }
    };

    this.ws.onclose = () => {
      this.emit("connection", { status: "disconnected" });
      if (this.shouldReconnect) {
        this.scheduleReconnect();
      }
    };

    this.ws.onerror = () => {
      this.ws?.close();
    };
  }

  private scheduleReconnect() {
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
    this.reconnectTimer = setTimeout(() => {
      this.doConnect();
      this.reconnectDelay = Math.min(
        this.reconnectDelay * 2,
        this.maxDelay
      );
    }, this.reconnectDelay);
  }

  private emit(channel: string, data: unknown) {
    const cbs = this.listeners.get(channel);
    if (cbs) {
      cbs.forEach((cb) => cb(data));
    }
  }

  subscribe(channel: string, callback: Callback) {
    if (!this.listeners.has(channel)) {
      this.listeners.set(channel, new Set());
    }
    this.listeners.get(channel)!.add(callback);
  }

  unsubscribe(channel: string, callback: Callback) {
    this.listeners.get(channel)?.delete(callback);
  }

  disconnect() {
    this.shouldReconnect = false;
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    if (this.ws) {
      this.ws.onclose = null;
      this.ws.close();
      this.ws = null;
    }
    this.emit("connection", { status: "disconnected" });
  }

  get connected(): boolean {
    return this.ws?.readyState === WebSocket.OPEN;
  }
}

let wsInstance: QBitWebSocket | null = null;

export function getWebSocket(): QBitWebSocket {
  if (!wsInstance) {
    wsInstance = new QBitWebSocket();
  }
  return wsInstance;
}
