"use client";

import { useState } from "react";
import { Plus } from "lucide-react";
import { getApi } from "@/lib/api";
import { useAppStore } from "@/lib/store";
import { Loading } from "@/components/ui/Loading";

export function CreateWallet({ onCreated }: { onCreated: () => void }) {
  const [loading, setLoading] = useState(false);
  const addToast = useAppStore((s) => s.addToast);

  const handleCreate = async () => {
    setLoading(true);
    try {
      const wallet = await getApi().createWallet();
      addToast(`Wallet created: ${wallet.address.slice(0, 16)}...`, "success");
      onCreated();
    } catch (err: unknown) {
      addToast(
        `Failed to create wallet: ${err instanceof Error ? err.message : String(err)}`,
        "error"
      );
    } finally {
      setLoading(false);
    }
  };

  return (
    <button
      onClick={handleCreate}
      disabled={loading}
      className="flex items-center gap-2 px-4 py-2.5 bg-accent text-primary rounded-lg text-sm font-medium hover:bg-accent/90 transition-colors disabled:opacity-50"
    >
      {loading ? (
        <Loading size="sm" />
      ) : (
        <>
          <Plus size={16} />
          Create Wallet
        </>
      )}
    </button>
  );
}
