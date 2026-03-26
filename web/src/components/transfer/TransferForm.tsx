"use client";

import { useState, useCallback, useEffect } from "react";
import { useSearchParams } from "next/navigation";
import { Card } from "@/components/ui/Card";
import { Loading } from "@/components/ui/Loading";
import { getApi } from "@/lib/api";
import { useApi } from "@/hooks/useApi";
import { parseQBIT, formatQBIT, estimateFee, TX_FEE_DISPLAY, TX_FEE_QUBITS } from "@/lib/format";
import { useAppStore } from "@/lib/store";
import type { WalletsResponse, FeeInfo } from "@/lib/types";
import { ArrowUpRight, AlertTriangle, CheckCircle } from "lucide-react";

export function TransferForm() {
  const searchParams = useSearchParams();
  const api = getApi();
  const addToast = useAppStore((s) => s.addToast);

  // Fetch wallets for dropdown
  const walletsFetcher = useCallback(() => api.listWallets(), [api]);
  const { data: walletsData, loading: walletsLoading } =
    useApi<WalletsResponse>(walletsFetcher);

  // Fetch dynamic fee info
  const feeFetcher = useCallback(async (): Promise<FeeInfo | null> => {
    try {
      return await api.getFeeInfo();
    } catch {
      return null;
    }
  }, [api]);
  const { data: feeData } = useApi<FeeInfo | null>(feeFetcher);

  const [fromWallet, setFromWallet] = useState("");
  const [toAddress, setToAddress] = useState("");
  const [amountStr, setAmountStr] = useState("");
  const [memo, setMemo] = useState("");
  const [priorityFee, setPriorityFee] = useState(1);
  const [showConfirm, setShowConfirm] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [result, setResult] = useState<{ tx_id: string } | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Pre-fill from query param
  useEffect(() => {
    const from = searchParams.get("from");
    if (from) setFromWallet(from);
  }, [searchParams]);

  // Update suggested priority fee when fee data loads
  useEffect(() => {
    if (feeData?.suggested_priority_fee) {
      setPriorityFee(feeData.suggested_priority_fee);
    }
  }, [feeData]);

  const isDynamic = feeData?.fee_model === "dynamic";
  const baseFee = feeData?.base_fee ?? 0;
  const transferWeight = feeData?.weights?.TRANSFER ?? 100_000;

  const amountQubits = parseQBIT(amountStr);
  const dynamicFee = isDynamic
    ? estimateFee(baseFee, priorityFee, transferWeight)
    : TX_FEE_QUBITS;
  const totalCost = amountQubits + dynamicFee;

  const canSubmit =
    fromWallet.length > 0 &&
    toAddress.length > 0 &&
    amountQubits > 0 &&
    !submitting;

  function handleSubmitClick() {
    setError(null);
    setResult(null);
    setShowConfirm(true);
  }

  async function handleConfirm() {
    setShowConfirm(false);
    setSubmitting(true);
    setError(null);
    try {
      const res = await api.transfer(
        fromWallet,
        toAddress,
        amountQubits,
        memo.trim() || undefined
      );
      setResult(res);
      addToast(`Transfer submitted: ${res.tx_id.slice(0, 12)}...`, "success");
      setAmountStr("");
      setMemo("");
      setToAddress("");
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      setError(msg);
      addToast(`Transfer failed: ${msg}`, "error");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <>
      <Card>
        {walletsLoading ? (
          <Loading size="sm" />
        ) : (
          <form
            onSubmit={(e) => {
              e.preventDefault();
              handleSubmitClick();
            }}
            className="space-y-5"
          >
            {/* From Wallet */}
            <div>
              <label className="block text-sm text-muted mb-2">
                From Wallet
              </label>
              <select
                value={fromWallet}
                onChange={(e) => setFromWallet(e.target.value)}
                className="w-full bg-primary border border-card-border rounded-lg px-4 py-2.5 text-sm text-foreground font-mono focus:outline-none focus:border-accent"
              >
                <option value="">Select a wallet...</option>
                {(walletsData?.wallets ?? []).map((addr) => (
                  <option key={addr} value={addr}>
                    {addr.slice(0, 16)}...{addr.slice(-8)}
                  </option>
                ))}
              </select>
            </div>

            {/* To Address */}
            <div>
              <label className="block text-sm text-muted mb-2">
                To Address
              </label>
              <input
                type="text"
                value={toAddress}
                onChange={(e) => setToAddress(e.target.value)}
                placeholder="Recipient address"
                className="w-full bg-primary border border-card-border rounded-lg px-4 py-2.5 text-sm text-foreground font-mono placeholder:text-muted/50 focus:outline-none focus:border-accent"
              />
            </div>

            {/* Amount */}
            <div>
              <label className="block text-sm text-muted mb-2">
                Amount (QBIT)
              </label>
              <input
                type="text"
                inputMode="decimal"
                value={amountStr}
                onChange={(e) => {
                  const val = e.target.value;
                  if (val === "" || /^\d*\.?\d{0,8}$/.test(val)) {
                    setAmountStr(val);
                  }
                }}
                placeholder="0.00"
                className="w-full bg-primary border border-card-border rounded-lg px-4 py-2.5 text-sm text-foreground font-mono placeholder:text-muted/50 focus:outline-none focus:border-accent"
              />
              <div className="mt-2 text-xs text-muted space-y-1">
                {isDynamic ? (
                  <>
                    <div className="flex items-center justify-between">
                      <span>Base fee: {baseFee} qubits/weight</span>
                      <span>Est. fee: {formatQBIT(dynamicFee)}</span>
                    </div>
                  </>
                ) : (
                  <div className="flex items-center justify-between">
                    <span>Network fee: {TX_FEE_DISPLAY}</span>
                  </div>
                )}
                {amountQubits > 0 && (
                  <div className="flex items-center justify-between">
                    <span></span>
                    <span className="font-medium">Total: {formatQBIT(totalCost)}</span>
                  </div>
                )}
              </div>
            </div>

            {/* Priority Fee (dynamic mode) */}
            {isDynamic && (
              <div>
                <label className="block text-sm text-muted mb-2">
                  Priority Fee <span className="text-muted/50">(qubits/weight)</span>
                </label>
                <input
                  type="range"
                  min={0}
                  max={Math.max(baseFee, 100)}
                  step={1}
                  value={priorityFee}
                  onChange={(e) => setPriorityFee(Number(e.target.value))}
                  className="w-full accent-accent"
                />
                <div className="mt-1 text-xs text-muted flex items-center justify-between">
                  <span>0</span>
                  <span className="font-mono">{priorityFee} qubits/weight</span>
                  <span>{Math.max(baseFee, 100)}</span>
                </div>
              </div>
            )}

            {/* Memo */}
            <div>
              <label className="block text-sm text-muted mb-2">
                Memo <span className="text-muted/50">(optional)</span>
              </label>
              <textarea
                value={memo}
                onChange={(e) => setMemo(e.target.value)}
                placeholder="Add a note to this transfer..."
                rows={3}
                maxLength={256}
                className="w-full bg-primary border border-card-border rounded-lg px-4 py-2.5 text-sm text-foreground placeholder:text-muted/50 focus:outline-none focus:border-accent resize-none"
              />
              <div className="text-xs text-muted mt-1 text-right">
                {memo.length}/256
              </div>
            </div>

            {/* Submit */}
            <button
              type="submit"
              disabled={!canSubmit}
              className="w-full flex items-center justify-center gap-2 px-4 py-3 bg-accent text-primary rounded-lg text-sm font-semibold hover:bg-accent/90 transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
            >
              {submitting ? (
                <Loading size="sm" />
              ) : (
                <>
                  <ArrowUpRight size={16} />
                  Send QBIT
                </>
              )}
            </button>

            {/* Error */}
            {error && (
              <div className="flex items-start gap-2 p-3 bg-error/10 border border-error/20 rounded-lg text-sm text-error">
                <AlertTriangle size={16} className="mt-0.5 shrink-0" />
                <span>{error}</span>
              </div>
            )}

            {/* Success */}
            {result && (
              <div className="flex items-start gap-2 p-3 bg-success/10 border border-success/20 rounded-lg text-sm text-success">
                <CheckCircle size={16} className="mt-0.5 shrink-0" />
                <div>
                  <div>Transfer submitted successfully.</div>
                  <div className="font-mono text-xs mt-1 break-all">
                    TX ID: {result.tx_id}
                  </div>
                </div>
              </div>
            )}
          </form>
        )}
      </Card>

      {/* Confirmation Dialog */}
      {showConfirm && (
        <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4">
          <div className="bg-card border border-card-border rounded-lg p-6 max-w-md w-full space-y-4">
            <h3 className="text-lg font-semibold text-foreground">
              Confirm Transfer
            </h3>
            <div className="space-y-2 text-sm">
              <div className="flex justify-between">
                <span className="text-muted">From</span>
                <span className="font-mono text-foreground">
                  {fromWallet.slice(0, 12)}...{fromWallet.slice(-6)}
                </span>
              </div>
              <div className="flex justify-between">
                <span className="text-muted">To</span>
                <span className="font-mono text-foreground">
                  {toAddress.slice(0, 12)}...{toAddress.slice(-6)}
                </span>
              </div>
              <div className="flex justify-between">
                <span className="text-muted">Amount</span>
                <span className="font-mono font-semibold text-foreground">
                  {formatQBIT(amountQubits)}
                </span>
              </div>
              <div className="flex justify-between">
                <span className="text-muted">Fee{isDynamic ? " (dynamic)" : ""}</span>
                <span className="font-mono text-muted">{formatQBIT(dynamicFee)}</span>
              </div>
              <div className="border-t border-card-border pt-2 flex justify-between">
                <span className="text-muted font-medium">Total</span>
                <span className="font-mono font-bold text-accent">
                  {formatQBIT(totalCost)}
                </span>
              </div>
              {memo.trim() && (
                <div className="pt-2">
                  <span className="text-muted">Memo:</span>
                  <p className="text-foreground mt-1 text-xs">{memo.trim()}</p>
                </div>
              )}
            </div>
            <div className="flex gap-3 pt-2">
              <button
                onClick={() => setShowConfirm(false)}
                className="flex-1 px-4 py-2.5 bg-card-border/30 text-foreground rounded-lg text-sm font-medium hover:bg-card-border/50 transition-colors"
              >
                Cancel
              </button>
              <button
                onClick={handleConfirm}
                className="flex-1 px-4 py-2.5 bg-accent text-primary rounded-lg text-sm font-semibold hover:bg-accent/90 transition-colors"
              >
                Confirm Send
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
