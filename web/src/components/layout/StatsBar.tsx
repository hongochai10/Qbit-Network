"use client";

import { useCallback } from "react";
import { Blocks, ArrowLeftRight, Users, Shield, Clock, Layers, Coins, Flame } from "lucide-react";
import { useAppStore } from "@/lib/store";
import { getApi } from "@/lib/api";
import { useApi } from "@/hooks/useApi";
import { formatQBIT } from "@/lib/format";
import type { SupplyInfo, FeeInfo } from "@/lib/types";

export function StatsBar() {
  const { chainHeight, pendingTxs, peerCount, validatorCount, currentEpoch } =
    useAppStore();

  const api = getApi();
  const supplyFetcher = useCallback(async (): Promise<SupplyInfo | null> => {
    try {
      return await api.getSupply();
    } catch {
      return null;
    }
  }, [api]);
  const { data: supplyData } = useApi(supplyFetcher);

  const feeFetcher = useCallback(async (): Promise<FeeInfo | null> => {
    try {
      return await api.getFeeInfo();
    } catch {
      return null;
    }
  }, [api]);
  const { data: feeData } = useApi(feeFetcher);

  const circulatingDisplay = supplyData
    ? formatQBIT(supplyData.circulating)
    : "--";

  const baseFeeDisplay = feeData
    ? `${feeData.base_fee}`
    : "--";

  const stats = [
    { label: "Chain Height", value: chainHeight, icon: Blocks, color: "text-accent" },
    { label: "Pending TXs", value: pendingTxs, icon: ArrowLeftRight, color: "text-warning" },
    { label: "Peers", value: peerCount, icon: Users, color: "text-success" },
    { label: "Validators", value: validatorCount, icon: Shield, color: "text-purple-400" },
    { label: "Epoch", value: currentEpoch, icon: Layers, color: "text-blue-400" },
    { label: "Block Time", value: "~5s", icon: Clock, color: "text-muted" },
    { label: "Circulating", value: circulatingDisplay, icon: Coins, color: "text-emerald-400" },
    { label: "Base Fee", value: baseFeeDisplay, icon: Flame, color: "text-orange-400" },
  ];

  return (
    <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-8 gap-3">
      {stats.map(({ label, value, icon: Icon, color }) => (
        <div
          key={label}
          className="bg-card border border-card-border rounded-lg p-4 flex items-center gap-3"
        >
          <Icon size={20} className={color} />
          <div className="min-w-0">
            <div className="text-xs text-muted">{label}</div>
            <div className="text-base font-semibold text-foreground font-mono truncate">
              {value}
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}
