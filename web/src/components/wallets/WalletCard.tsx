"use client";

import { HashDisplay } from "@/components/ui/HashDisplay";
import { Wallet } from "lucide-react";

export function WalletCard({ address }: { address: string }) {
  return (
    <div className="bg-card border border-card-border rounded-lg p-4 hover:border-accent/30 transition-colors">
      <div className="flex items-center gap-3">
        <div className="h-10 w-10 rounded-lg bg-accent/10 flex items-center justify-center">
          <Wallet size={18} className="text-accent" />
        </div>
        <div className="flex-1 min-w-0">
          <div className="text-xs text-muted mb-1">Address</div>
          <HashDisplay hash={address} truncate={12} />
        </div>
      </div>
    </div>
  );
}
