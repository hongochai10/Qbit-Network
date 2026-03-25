"use client";

import { useCallback } from "react";
import { getApi } from "@/lib/api";
import { useApi } from "@/hooks/useApi";
import { HashDisplay } from "@/components/ui/HashDisplay";
import { Badge } from "@/components/ui/Badge";
import { Card } from "@/components/ui/Card";
import { Loading } from "@/components/ui/Loading";

export function TxDetail({ txId }: { txId: string }) {
  const api = getApi();
  const fetcher = useCallback(() => api.getTx(txId), [api, txId]);
  const { data: tx, loading, error } = useApi(fetcher, [txId]);

  if (loading) return <Loading />;
  if (error) return <div className="text-error p-4">Error: {error}</div>;
  if (!tx) return null;

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
