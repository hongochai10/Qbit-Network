"use client";

import { useCallback } from "react";
import { getApi } from "@/lib/api";
import { useApi } from "@/hooks/useApi";
import { HashDisplay } from "@/components/ui/HashDisplay";
import { Badge } from "@/components/ui/Badge";
import { Card } from "@/components/ui/Card";
import { Loading } from "@/components/ui/Loading";
import { formatQBIT } from "@/lib/format";

export function TxDetail({ txId }: { txId: string }) {
  const api = getApi();
  const fetcher = useCallback(() => api.getTx(txId), [api, txId]);
  const { data: tx, loading, error } = useApi(fetcher, [txId]);

  if (loading) return <Loading />;
  if (error) return <div className="text-error p-4">Error: {error}</div>;
  if (!tx) return null;

  const isTransfer = tx.type === "TRANSFER";
  const amount =
    typeof tx.payload?.amount === "number" ? tx.payload.amount : null;
  const fee =
    typeof tx.payload?.fee === "number" ? tx.payload.fee : null;
  const memo =
    typeof tx.payload?.memo === "string" && tx.payload.memo
      ? tx.payload.memo
      : null;

  return (
    <div className="space-y-6">
      <Card title="Transaction Details">
        <dl className="grid grid-cols-1 md:grid-cols-2 gap-4 text-sm">
          <div className="md:col-span-2">
            <dt className="text-muted mb-1">TX ID</dt>
            <dd>
              <HashDisplay hash={tx.tx_id} truncate={20} />
            </dd>
          </div>
          <div>
            <dt className="text-muted mb-1">Type</dt>
            <dd>
              <Badge label={tx.type} />
            </dd>
          </div>
          <div>
            <dt className="text-muted mb-1">Nonce</dt>
            <dd className="font-mono text-foreground">{tx.nonce}</dd>
          </div>
          <div className="md:col-span-2">
            <dt className="text-muted mb-1">From</dt>
            <dd>
              <HashDisplay hash={tx.from} truncate={16} />
            </dd>
          </div>
          {tx.to && (
            <div className="md:col-span-2">
              <dt className="text-muted mb-1">To</dt>
              <dd>
                <HashDisplay hash={tx.to} truncate={16} />
              </dd>
            </div>
          )}
          <div>
            <dt className="text-muted mb-1">Timestamp</dt>
            <dd className="text-foreground">
              {new Date(tx.timestamp * 1000).toLocaleString()}
            </dd>
          </div>
          <div>
            <dt className="text-muted mb-1">Chain ID</dt>
            <dd className="font-mono text-foreground">{tx.chainId}</dd>
          </div>
        </dl>
      </Card>

      {/* Amount + Fee card for transfers */}
      {(isTransfer && amount !== null) && (
        <Card title="Transfer Details">
          <div className="space-y-4">
            <div className="flex items-center justify-between p-4 bg-primary/50 rounded-lg border border-card-border">
              <span className="text-muted text-sm">Amount</span>
              <span className="text-2xl font-bold font-mono text-emerald-400">
                {formatQBIT(amount)}
              </span>
            </div>
            {fee !== null && (
              <div className="flex items-center justify-between text-sm">
                <span className="text-muted">Fee</span>
                <span className="font-mono text-muted">{formatQBIT(fee)}</span>
              </div>
            )}
            {memo && (
              <div>
                <div className="text-muted text-sm mb-1">Memo</div>
                <div className="bg-primary/50 rounded-lg p-3 text-sm text-foreground border border-card-border">
                  {memo}
                </div>
              </div>
            )}
          </div>
        </Card>
      )}

      {/* Fee for non-transfer types */}
      {!isTransfer && fee !== null && (
        <Card title="Fee">
          <div className="flex items-center justify-between text-sm">
            <span className="text-muted">Transaction Fee</span>
            <span className="font-mono text-foreground">{formatQBIT(fee)}</span>
          </div>
        </Card>
      )}

      <Card title="Payload">
        <pre className="text-xs font-mono text-muted bg-primary rounded-lg p-4 overflow-x-auto">
          {JSON.stringify(tx.payload, null, 2)}
        </pre>
      </Card>

      <Card title="Cryptographic Data">
        <dl className="space-y-3 text-sm">
          <div>
            <dt className="text-muted mb-1">Signature</dt>
            <dd className="break-all">
              <HashDisplay hash={tx.signature} truncate={24} />
            </dd>
          </div>
          <div>
            <dt className="text-muted mb-1">Sender Public Key</dt>
            <dd className="break-all">
              <HashDisplay hash={tx.sender_pubkey} truncate={24} />
            </dd>
          </div>
        </dl>
      </Card>
    </div>
  );
}
