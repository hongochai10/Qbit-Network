"use client";

import { useState, useCallback } from "react";
import { getApi } from "@/lib/api";
import { useApi } from "@/hooks/useApi";
import { useAppStore } from "@/lib/store";

type Action = "stake" | "delegate" | "unstake";

export function StakeForm() {
  const [action, setAction] = useState<Action>("stake");
  const [wallet, setWallet] = useState("");
  const [validator, setValidator] = useState("");
  const [amount, setAmount] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const addToast = useAppStore((s) => s.addToast);

  const api = getApi();
  const walletsFetcher = useCallback(() => api.listWallets(), [api]);
  const validatorsFetcher = useCallback(() => api.getValidators(), [api]);
  const { data: walletsData } = useApi(walletsFetcher);
  const { data: validatorsData } = useApi(validatorsFetcher);

  const wallets = walletsData?.wallets ?? [];
  const validators = validatorsData?.validators ?? [];

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!wallet || !validator || !amount) return;

    const amt = Number(amount);
    if (isNaN(amt) || amt <= 0) {
      addToast("Amount must be a positive number", "error");
      return;
    }

    setSubmitting(true);
    try {
      const fn = action === "stake" ? api.stake : action === "delegate" ? api.delegate : api.unstake;
      const result = await fn.call(api, wallet, validator, amt);
      addToast(`${action} successful! TX: ${result.tx_id.slice(0, 16)}...`, "success");
      setAmount("");
    } catch (err: unknown) {
      addToast(
        `${action} failed: ${err instanceof Error ? err.message : String(err)}`,
        "error"
      );
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <form onSubmit={handleSubmit} className="space-y-4">
      {/* Action tabs */}
      <div className="flex gap-1 bg-primary rounded-lg p-1">
        {(["stake", "delegate", "unstake"] as Action[]).map((a) => (
          <button
            key={a}
            type="button"
            onClick={() => setAction(a)}
            className={`flex-1 py-2 text-sm rounded-md capitalize transition-colors ${
              action === a
                ? "bg-accent text-primary font-medium"
                : "text-muted hover:text-foreground"
            }`}
          >
            {a}
          </button>
        ))}
      </div>

      <div>
        <label className="block text-sm text-muted mb-2">Your Wallet</label>
        <select
          value={wallet}
          onChange={(e) => setWallet(e.target.value)}
          className="w-full bg-primary border border-card-border rounded-lg py-2.5 px-4 text-sm text-foreground focus:outline-none focus:border-accent"
        >
          <option value="">Select wallet...</option>
          {wallets.map((addr) => (
            <option key={addr} value={addr}>
              {addr.slice(0, 16)}...{addr.slice(-8)}
            </option>
          ))}
        </select>
      </div>

      <div>
        <label className="block text-sm text-muted mb-2">Validator</label>
        <select
          value={validator}
          onChange={(e) => setValidator(e.target.value)}
          className="w-full bg-primary border border-card-border rounded-lg py-2.5 px-4 text-sm text-foreground focus:outline-none focus:border-accent"
        >
          <option value="">Select validator...</option>
          {validators.map((addr) => (
            <option key={addr} value={addr}>
              {addr.slice(0, 16)}...{addr.slice(-8)}
            </option>
          ))}
        </select>
      </div>

      <div>
        <label className="block text-sm text-muted mb-2">Amount</label>
        <input
          type="number"
          value={amount}
          onChange={(e) => setAmount(e.target.value)}
          placeholder="0"
          min="1"
          step="1"
          className="w-full bg-primary border border-card-border rounded-lg py-2.5 px-4 text-sm font-mono text-foreground placeholder:text-muted focus:outline-none focus:border-accent"
        />
      </div>

      <button
        type="submit"
        disabled={submitting || !wallet || !validator || !amount}
        className="w-full py-2.5 bg-accent text-primary rounded-lg text-sm font-medium hover:bg-accent/90 transition-colors disabled:opacity-50 capitalize"
      >
        {submitting ? "Processing..." : action}
      </button>
    </form>
  );
}
