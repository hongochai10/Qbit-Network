"use client";

import { useCallback } from "react";
import Link from "next/link";
import { getApi } from "@/lib/api";
import { useApi } from "@/hooks/useApi";
import { HashDisplay } from "@/components/ui/HashDisplay";
import { Badge } from "@/components/ui/Badge";
import { Card } from "@/components/ui/Card";
import { Loading } from "@/components/ui/Loading";
import { formatQBIT } from "@/lib/format";

export function BlockDetail({ index }: { index: number }) {
  const api = getApi();
  const fetcher = useCallback(() => api.getBlock(index), [api, index]);
  const { data: block, loading, error } = useApi(fetcher, [index]);

  if (loading) return <Loading />;
  if (error) return <div className="text-error p-4">Error: {error}</div>;
  if (!block) return null;

  return (
    <div className="space-y-6">
      <Card title="Block Information">
        <dl className="grid grid-cols-1 md:grid-cols-2 gap-4 text-sm">
          <div>
            <dt className="text-muted mb-1">Index</dt>
            <dd className="font-mono text-foreground">#{block.index}</dd>
          </div>
          <div>
            <dt className="text-muted mb-1">Timestamp</dt>
            <dd className="text-foreground">
              {new Date(block.timestamp * 1000).toLocaleString()}
            </dd>
          </div>
          <div className="md:col-span-2">
            <dt className="text-muted mb-1">Hash</dt>
            <dd>
              <HashDisplay hash={block.hash} truncate={20} />
            </dd>
          </div>
          <div className="md:col-span-2">
            <dt className="text-muted mb-1">Previous Hash</dt>
            <dd>
              <HashDisplay hash={block.prevHash} truncate={20} />
            </dd>
          </div>
          <div className="md:col-span-2">
            <dt className="text-muted mb-1">Merkle Root</dt>
            <dd>
              <HashDisplay hash={block.merkleRoot} truncate={20} />
            </dd>
          </div>
          <div className="md:col-span-2">
            <dt className="text-muted mb-1">Validator</dt>
            <dd>
              <HashDisplay hash={block.validator} truncate={12} />
            </dd>
          </div>
          {/* Base Fee */}
          {typeof block.baseFee === "number" && block.baseFee !== 0 && (
            <div>
              <dt className="text-muted mb-1">Base Fee</dt>
              <dd className="font-mono text-orange-400">
                {block.baseFee} qubits/weight
              </dd>
            </div>
          )}
          {/* Block Reward */}
          {(() => {
            const coinbaseTx = block.transactions.find(
              (tx) => tx.type === "COINBASE" || tx.type === "REWARD"
            );
            const rewardAmount =
              coinbaseTx && typeof coinbaseTx.payload?.amount === "number"
                ? coinbaseTx.payload.amount
                : null;
            return rewardAmount !== null ? (
              <div>
                <dt className="text-muted mb-1">Block Reward</dt>
                <dd className="font-mono text-emerald-400 font-semibold">
                  {formatQBIT(rewardAmount)}
                </dd>
              </div>
            ) : null;
          })()}
        </dl>
      </Card>

      <Card title={`Transactions (${block.transactions.length})`}>
        {block.transactions.length === 0 ? (
          <p className="text-muted text-sm">No transactions in this block.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-card-border text-left text-muted">
                  <th className="py-2 px-3 font-medium">TX ID</th>
                  <th className="py-2 px-3 font-medium">Type</th>
                  <th className="py-2 px-3 font-medium">From</th>
                  <th className="py-2 px-3 font-medium">Nonce</th>
                </tr>
              </thead>
              <tbody>
                {block.transactions.map((tx) => (
                  <tr
                    key={tx.tx_id}
                    className="border-b border-card-border/50 hover:bg-white/5"
                  >
                    <td className="py-2 px-3">
                      <Link
                        href={`/transactions/${tx.tx_id}`}
                        className="text-accent hover:underline"
                      >
                        <HashDisplay hash={tx.tx_id} truncate={6} />
                      </Link>
                    </td>
                    <td className="py-2 px-3">
                      <Badge label={tx.type} />
                    </td>
                    <td className="py-2 px-3">
                      <HashDisplay hash={tx.from} truncate={6} />
                    </td>
                    <td className="py-2 px-3 font-mono text-muted">{tx.nonce}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}
