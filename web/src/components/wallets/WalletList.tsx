"use client";

import { useCallback } from "react";
import { getApi } from "@/lib/api";
import { useApi } from "@/hooks/useApi";
import { WalletCard } from "./WalletCard";
import { CreateWallet } from "./CreateWallet";
import { Loading } from "@/components/ui/Loading";

export function WalletList() {
  const api = getApi();
  const fetcher = useCallback(() => api.listWallets(), [api]);
  const { data, loading, error, refetch } = useApi(fetcher);

  if (loading) return <Loading />;
  if (error) return <div className="text-error p-4">Error: {error}</div>;

  const wallets = data?.wallets ?? [];

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <span className="text-sm text-muted">{wallets.length} wallet(s)</span>
        <CreateWallet onCreated={refetch} />
      </div>
      {wallets.length === 0 ? (
        <div className="text-center py-12 text-muted">
          No wallets yet. Create one to get started.
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
          {wallets.map((addr) => (
            <WalletCard key={addr} address={addr} />
          ))}
        </div>
      )}
    </div>
  );
}
