"use client";

import { useState } from "react";
import { Search, CheckCircle, XCircle } from "lucide-react";
import { getApi } from "@/lib/api";
import { useAppStore } from "@/lib/store";

interface VerifyResult {
  verified: boolean;
  proof: unknown;
}

export function VerifyForm() {
  const [hash, setHash] = useState("");
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<VerifyResult | null>(null);
  const addToast = useAppStore((s) => s.addToast);

  const handleVerify = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!hash.trim()) return;
    setLoading(true);
    setResult(null);
    try {
      const res = await getApi().verify(hash.trim());
      setResult(res);
    } catch (err: unknown) {
      addToast(
        `Verification failed: ${err instanceof Error ? err.message : String(err)}`,
        "error"
      );
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="space-y-5">
      <form onSubmit={handleVerify} className="flex gap-2">
        <input
          type="text"
          value={hash}
          onChange={(e) => setHash(e.target.value)}
          placeholder="Enter document hash to verify..."
          className="flex-1 bg-primary border border-card-border rounded-lg py-2.5 px-4 text-sm font-mono text-foreground placeholder:text-muted focus:outline-none focus:border-accent"
        />
        <button
          type="submit"
          disabled={loading || !hash.trim()}
          className="flex items-center gap-2 px-4 py-2.5 bg-accent text-primary rounded-lg text-sm font-medium hover:bg-accent/90 transition-colors disabled:opacity-50"
        >
          <Search size={16} />
          {loading ? "Verifying..." : "Verify"}
        </button>
      </form>

      {result && (
        <div
          className={`flex items-center gap-3 p-4 rounded-lg border ${
            result.verified
              ? "border-success/30 bg-success/10"
              : "border-error/30 bg-error/10"
          }`}
        >
          {result.verified ? (
            <>
              <CheckCircle size={20} className="text-success" />
              <div>
                <div className="text-sm font-medium text-success">
                  Document Verified
                </div>
                <div className="text-xs text-muted mt-1">
                  This document has been notarized on the QBit blockchain.
                </div>
              </div>
            </>
          ) : (
            <>
              <XCircle size={20} className="text-error" />
              <div>
                <div className="text-sm font-medium text-error">
                  Not Found
                </div>
                <div className="text-xs text-muted mt-1">
                  No notarization record found for this hash.
                </div>
              </div>
            </>
          )}
        </div>
      )}

      {result !== null && result.proof != null && (
        <div>
          <h4 className="text-sm text-muted mb-2">Proof Data</h4>
          <pre className="text-xs font-mono text-muted bg-primary rounded-lg p-4 overflow-x-auto">
            {JSON.stringify(result.proof, null, 2)}
          </pre>
        </div>
      )}
    </div>
  );
}
