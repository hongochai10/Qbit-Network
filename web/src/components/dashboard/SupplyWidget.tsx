"use client";

import { useCallback } from "react";
import { getApi } from "@/lib/api";
import { useApi } from "@/hooks/useApi";
import { Card } from "@/components/ui/Card";
import { Loading } from "@/components/ui/Loading";
import { formatQBIT } from "@/lib/format";
import type { SupplyInfo } from "@/lib/types";
import { Coins, Flame, Lock, TrendingUp } from "lucide-react";

export function SupplyWidget() {
  const api = getApi();
  const fetcher = useCallback(async (): Promise<SupplyInfo | null> => {
    try {
      return await api.getSupply();
    } catch {
      return null;
    }
  }, [api]);
  const { data, loading } = useApi<SupplyInfo | null>(fetcher);

  if (loading) {
    return (
      <Card title="Supply Overview">
        <Loading size="sm" />
      </Card>
    );
  }

  if (!data) {
    return (
      <Card title="Supply Overview">
        <p className="text-muted text-sm">Supply data unavailable.</p>
      </Card>
    );
  }

  const mintedPercent =
    data.max_supply > 0
      ? Math.min((data.total_minted / data.max_supply) * 100, 100)
      : 0;

  const breakdown = [
    {
      label: "Circulating",
      value: data.circulating,
      icon: TrendingUp,
      color: "text-emerald-400",
    },
    {
      label: "Burned",
      value: data.total_burned,
      icon: Flame,
      color: "text-orange-400",
    },
    {
      label: "Staked",
      value: data.staked ?? 0,
      icon: Lock,
      color: "text-blue-400",
    },
  ];

  return (
    <Card title="Supply Overview">
      <div className="space-y-5">
        {/* Progress bar: Minted / Max Supply */}
        <div>
          <div className="flex items-center justify-between text-sm mb-2">
            <span className="text-muted flex items-center gap-1.5">
              <Coins size={14} className="text-accent" />
              Total Minted
            </span>
            <span className="font-mono text-foreground">
              {formatQBIT(data.total_minted)}
            </span>
          </div>
          <div className="w-full bg-primary rounded-full h-3 border border-card-border overflow-hidden">
            <div
              className="h-full bg-accent rounded-full transition-all duration-500"
              style={{ width: `${mintedPercent}%` }}
            />
          </div>
          <div className="flex items-center justify-between text-xs text-muted mt-1">
            <span>{mintedPercent.toFixed(1)}% of max supply</span>
            <span className="font-mono">{formatQBIT(data.max_supply)}</span>
          </div>
        </div>

        {/* Breakdown */}
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
          {breakdown.map(({ label, value, icon: Icon, color }) => (
            <div
              key={label}
              className="bg-primary/50 rounded-lg p-3 border border-card-border"
            >
              <div className="flex items-center gap-2 mb-1">
                <Icon size={14} className={color} />
                <span className="text-xs text-muted">{label}</span>
              </div>
              <div className="font-mono text-sm font-semibold text-foreground">
                {formatQBIT(value)}
              </div>
            </div>
          ))}
        </div>
      </div>
    </Card>
  );
}
