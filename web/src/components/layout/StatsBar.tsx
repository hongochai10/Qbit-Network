"use client";

import { Blocks, ArrowLeftRight, Users, Shield, Clock, Layers } from "lucide-react";
import { useAppStore } from "@/lib/store";

export function StatsBar() {
  const { chainHeight, pendingTxs, peerCount, validatorCount, currentEpoch } =
    useAppStore();

  const stats = [
    { label: "Chain Height", value: chainHeight, icon: Blocks, color: "text-accent" },
    { label: "Pending TXs", value: pendingTxs, icon: ArrowLeftRight, color: "text-warning" },
    { label: "Peers", value: peerCount, icon: Users, color: "text-success" },
    { label: "Validators", value: validatorCount, icon: Shield, color: "text-purple-400" },
    { label: "Epoch", value: currentEpoch, icon: Layers, color: "text-blue-400" },
    { label: "Block Time", value: "~5s", icon: Clock, color: "text-muted" },
  ];

  return (
    <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
      {stats.map(({ label, value, icon: Icon, color }) => (
        <div
          key={label}
          className="bg-card border border-card-border rounded-lg p-4 flex items-center gap-3"
        >
          <Icon size={20} className={color} />
          <div>
            <div className="text-xs text-muted">{label}</div>
            <div className="text-base font-semibold text-foreground font-mono">
              {value}
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}
