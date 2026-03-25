"use client";

import { useState, useCallback } from "react";
import { Upload, FileCheck } from "lucide-react";
import { getApi } from "@/lib/api";
import { useApi } from "@/hooks/useApi";
import { useAppStore } from "@/lib/store";

async function hashFile(file: File): Promise<string> {
  const buffer = await file.arrayBuffer();
  const hashBuffer = await crypto.subtle.digest("SHA-256", buffer);
  const hashArray = Array.from(new Uint8Array(hashBuffer));
  return hashArray.map((b) => b.toString(16).padStart(2, "0")).join("");
}

export function NotarizeForm() {
  const [hash, setHash] = useState("");
  const [metadata, setMetadata] = useState("");
  const [wallet, setWallet] = useState("");
  const [fileName, setFileName] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const addToast = useAppStore((s) => s.addToast);

  const api = getApi();
  const walletsFetcher = useCallback(() => api.listWallets(), [api]);
  const { data: walletsData } = useApi(walletsFetcher);
  const wallets = walletsData?.wallets ?? [];

  const handleFile = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setFileName(file.name);
    const h = await hashFile(file);
    setHash(h);
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!hash || !wallet) {
      addToast("Please select a wallet and provide a document hash", "error");
      return;
    }
    setSubmitting(true);
    try {
      const result = await api.notarize(wallet, hash, metadata);
      addToast(`Notarized! TX: ${result.tx_id.slice(0, 16)}...`, "success");
      setHash("");
      setMetadata("");
      setFileName("");
    } catch (err: unknown) {
      addToast(
        `Notarization failed: ${err instanceof Error ? err.message : String(err)}`,
        "error"
      );
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <form onSubmit={handleSubmit} className="space-y-5">
      {/* File upload */}
      <div>
        <label className="block text-sm text-muted mb-2">
          Upload Document (hashed client-side)
        </label>
        <label className="flex items-center gap-3 border border-dashed border-card-border rounded-lg p-6 cursor-pointer hover:border-accent/50 transition-colors">
          <Upload size={20} className="text-muted" />
          <span className="text-sm text-muted">
            {fileName || "Drop a file or click to browse"}
          </span>
          <input type="file" onChange={handleFile} className="hidden" />
        </label>
      </div>

      {/* Hash input */}
      <div>
        <label className="block text-sm text-muted mb-2">
          Document Hash (SHA-256)
        </label>
        <input
          type="text"
          value={hash}
          onChange={(e) => setHash(e.target.value)}
          placeholder="Enter or auto-computed from file..."
          className="w-full bg-primary border border-card-border rounded-lg py-2.5 px-4 text-sm font-mono text-foreground placeholder:text-muted focus:outline-none focus:border-accent"
        />
      </div>

      {/* Wallet select */}
      <div>
        <label className="block text-sm text-muted mb-2">Wallet</label>
        <select
          value={wallet}
          onChange={(e) => setWallet(e.target.value)}
          className="w-full bg-primary border border-card-border rounded-lg py-2.5 px-4 text-sm text-foreground focus:outline-none focus:border-accent"
        >
          <option value="">Select a wallet...</option>
          {wallets.map((addr) => (
            <option key={addr} value={addr}>
              {addr.slice(0, 16)}...{addr.slice(-8)}
            </option>
          ))}
        </select>
      </div>

      {/* Metadata */}
      <div>
        <label className="block text-sm text-muted mb-2">
          Metadata (optional)
        </label>
        <textarea
          value={metadata}
          onChange={(e) => setMetadata(e.target.value)}
          placeholder="Description, tags, etc."
          rows={3}
          className="w-full bg-primary border border-card-border rounded-lg py-2.5 px-4 text-sm text-foreground placeholder:text-muted focus:outline-none focus:border-accent resize-none"
        />
      </div>

      <button
        type="submit"
        disabled={submitting || !hash || !wallet}
        className="flex items-center gap-2 px-6 py-2.5 bg-accent text-primary rounded-lg text-sm font-medium hover:bg-accent/90 transition-colors disabled:opacity-50"
      >
        <FileCheck size={16} />
        {submitting ? "Notarizing..." : "Notarize Document"}
      </button>
    </form>
  );
}
