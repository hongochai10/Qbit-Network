"use client";

import { useCallback } from "react";
import Link from "next/link";
import { HashDisplay } from "@/components/ui/HashDisplay";
import { Wallet, Shield, FileText, Send, ArrowDownLeft, Layers, ArrowUpRight } from "lucide-react";
import { getApi } from "@/lib/api";
import { useApi } from "@/hooks/useApi";
import { formatQBIT } from "@/lib/format";
import type { BalanceInfo } from "@/lib/types";

interface AddressInfo {
  address: string;
  next_nonce: number;
  sent_tx_count: number;
  received_tx_count: number;
  notarization_count: number;
  is_validator: boolean;
  encryption_pk?: string;
}

export function WalletCard({ address }: { address: string }) {
  const api = getApi();
  const fetcher = useCallback(async (): Promise<AddressInfo> => {
    const res = await api.getAddress(address);
    return res as unknown as AddressInfo;
  }, [api, address]);
  const { data } = useApi<AddressInfo>(fetcher);

  // Fetch balance
  const balanceFetcher = useCallback(async (): Promise<BalanceInfo | null> => {
    try {
      return await api.getBalance(address);
    } catch {
      return null;
    }
  }, [api, address]);
  const { data: balanceData } = useApi(balanceFetcher);

  // Also fetch stake info if validator
  const stakeFetcher = useCallback(async (): Promise<{ total_stake: number } | null> => {
    try {
      const res = await api.getStakes(address);
      return res as unknown as { total_stake: number };
    } catch {
      return null;
    }
  }, [api, address]);
  const { data: stakeData } = useApi(stakeFetcher);

  const info = data as AddressInfo | null;
  const totalStake = stakeData?.total_stake ?? 0;
  const balance = balanceData?.balance ?? 0;

  return (
    <div className="bg-card border border-card-border rounded-lg p-5 hover:border-accent/30 transition-colors">
      {/* Header */}
      <div className="flex items-center gap-3 mb-4">
        <div className="h-10 w-10 rounded-lg bg-accent/10 flex items-center justify-center">
          <Wallet size={18} className="text-accent" />
        </div>
        <div className="flex-1 min-w-0">
          <div className="text-xs text-muted mb-1">Address</div>
          <HashDisplay hash={address} truncate={12} />
        </div>
        {info?.is_validator && (
          <span className="px-2 py-1 bg-green-500/20 text-green-400 text-xs rounded-full flex items-center gap-1">
            <Shield size={10} /> Validator
          </span>
        )}
      </div>

      {/* Balance Display */}
      <div className="mb-4 p-4 bg-primary/50 rounded-lg border border-card-border">
        <div className="text-xs text-muted mb-1">Balance</div>
        <div className="text-2xl font-bold text-foreground font-mono">
          {formatQBIT(balance)}
        </div>
        {totalStake > 0 && (
          <div className="text-xs text-muted mt-1">
            Staked: <span className="text-accent font-mono">{formatQBIT(totalStake)}</span>
          </div>
        )}
      </div>

      {/* Actions */}
      <div className="mb-4">
        <Link
          href={`/transfer?from=${encodeURIComponent(address)}`}
          className="inline-flex items-center gap-2 px-4 py-2 bg-accent/20 text-accent rounded-lg text-sm font-medium hover:bg-accent/30 transition-colors"
        >
          <ArrowUpRight size={14} />
          Send QBIT
        </Link>
      </div>

      {/* Stats Grid */}
      {info && (
        <div className="grid grid-cols-2 gap-3 mt-3 pt-3 border-t border-card-border">
          <div className="flex items-center gap-2">
            <Send size={14} className="text-muted" />
            <div>
              <div className="text-xs text-muted">Sent TXs</div>
              <div className="text-sm font-semibold">{info.sent_tx_count}</div>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <ArrowDownLeft size={14} className="text-muted" />
            <div>
              <div className="text-xs text-muted">Received</div>
              <div className="text-sm font-semibold">{info.received_tx_count}</div>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <FileText size={14} className="text-muted" />
            <div>
              <div className="text-xs text-muted">Notarizations</div>
              <div className="text-sm font-semibold">{info.notarization_count}</div>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <Layers size={14} className="text-muted" />
            <div>
              <div className="text-xs text-muted">Stake Weight</div>
              <div className="text-sm font-semibold text-accent">{totalStake}</div>
            </div>
          </div>
        </div>
      )}

      {/* Nonce */}
      {info && (
        <div className="mt-3 pt-3 border-t border-card-border text-xs text-muted">
          Nonce: {info.next_nonce}
        </div>
      )}
    </div>
  );
}
