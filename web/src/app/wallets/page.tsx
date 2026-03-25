"use client";

import { Header } from "@/components/layout/Header";
import { WalletList } from "@/components/wallets/WalletList";
import { Card } from "@/components/ui/Card";

export default function WalletsPage() {
  return (
    <div>
      <Header title="Wallets" />
      <div className="p-6">
        <Card>
          <WalletList />
        </Card>
      </div>
    </div>
  );
}
