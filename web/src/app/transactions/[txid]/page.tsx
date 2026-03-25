"use client";

import { use } from "react";
import { Header } from "@/components/layout/Header";
import { TxDetail } from "@/components/transactions/TxDetail";

export default function TxDetailPage({
  params,
}: {
  params: Promise<{ txid: string }>;
}) {
  const { txid } = use(params);

  return (
    <div>
      <Header title="Transaction Detail" />
      <div className="p-6">
        <TxDetail txId={txid} />
      </div>
    </div>
  );
}
