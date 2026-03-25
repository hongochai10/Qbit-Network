"use client";

import { useEffect } from "react";
import { getWebSocket } from "@/lib/websocket";

export function useWebSocket(
  channel: string,
  callback: (data: unknown) => void
) {
  useEffect(() => {
    const ws = getWebSocket();
    ws.subscribe(channel, callback);
    return () => {
      ws.unsubscribe(channel, callback);
    };
  }, [channel, callback]);
}
