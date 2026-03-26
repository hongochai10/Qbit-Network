"use client";

import { Suspense } from "react";
import { Header } from "@/components/layout/Header";
import { Loading } from "@/components/ui/Loading";
import { TransferForm } from "@/components/transfer/TransferForm";

export default function TransferPage() {
  return (
    <div>
      <Header title="Transfer QBIT" />
      <div className="p-6 max-w-2xl">
        <Suspense fallback={<Loading />}>
          <TransferForm />
        </Suspense>
      </div>
    </div>
  );
}
