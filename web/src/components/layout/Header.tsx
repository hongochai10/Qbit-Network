"use client";

import { useAppStore } from "@/lib/store";

export function Header({ title }: { title: string }) {
  const connected = useAppStore((s) => s.connected);
  const chainHeight = useAppStore((s) => s.chainHeight);
  const pendingTxs = useAppStore((s) => s.pendingTxs);
  const peerCount = useAppStore((s) => s.peerCount);

  return (
    <header className="h-16 border-b border-card-border flex items-center justify-between px-6 bg-card/50 backdrop-blur-sm">
      <h2 className="text-lg font-semibold text-foreground">{title}</h2>
      <div className="flex items-center gap-5 text-xs text-muted">
        <span>
          Height: <span className="text-foreground font-mono">{chainHeight}</span>
        </span>
        <span>
          Pool: <span className="text-foreground font-mono">{pendingTxs}</span>
        </span>
        <span>
          Peers: <span className="text-foreground font-mono">{peerCount}</span>
        </span>
        <div className="flex items-center gap-1.5">
          <div
            className={`h-2 w-2 rounded-full ${
              connected ? "bg-success" : "bg-error"
            }`}
          />
          <span>{connected ? "Online" : "Offline"}</span>
        </div>
      </div>
    </header>
  );
}
