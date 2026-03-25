"use client";

import { Header } from "@/components/layout/Header";
import { TxSearch } from "@/components/transactions/TxSearch";
import { Card } from "@/components/ui/Card";

export default function TransactionsPage() {
  return (
    <div>
      <Header title="Transactions" />
      <div className="p-6 space-y-6">
        <Card title="Search Transaction">
          <TxSearch />
        </Card>
        <Card>
          <p className="text-muted text-sm">
            Enter a transaction ID above to view its details, or browse
            transactions from the block explorer.
          </p>
        </Card>
      </div>
    </div>
  );
}
