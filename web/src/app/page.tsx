"use client";

import { useCallback } from "react";
import Link from "next/link";
import { Header } from "@/components/layout/Header";
import { StatsBar } from "@/components/layout/StatsBar";
import { Card } from "@/components/ui/Card";
import { HashDisplay } from "@/components/ui/HashDisplay";
import { Badge } from "@/components/ui/Badge";
import { Loading } from "@/components/ui/Loading";
import { getApi } from "@/lib/api";
import { useApi } from "@/hooks/useApi";

export default function DashboardPage() {
  const api = getApi();
  const blocksFetcher = useCallback(() => api.getBlocks(1, 10), [api]);
  const poolFetcher = useCallback(() => api.getPool(), [api]);
  const { data: blocksData, loading: blocksLoading } = useApi(blocksFetcher);
  const { data: poolData } = useApi(poolFetcher);

  return (
    <div>
      <Header title="Dashboard" />
      <div className="p-6 space-y-6">
        <StatsBar />

        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          {/* Recent Blocks */}
          <Card title="Recent Blocks" className="lg:col-span-2">
            {blocksLoading ? (
              <Loading size="sm" />
            ) : (
              <div className="space-y-2">
                {(blocksData?.blocks ?? []).map((block) => (
                  <Link
                    key={block.index}
                    href={`/blocks/${block.index}`}
                    className="flex items-center justify-between p-3 rounded-lg hover:bg-white/5 transition-colors"
                  >
                    <div className="flex items-center gap-3">
                      <div className="h-9 w-9 rounded bg-accent/10 flex items-center justify-center text-accent font-mono text-sm">
                        {block.index}
                      </div>
                      <div>
                        <HashDisplay hash={block.hash} truncate={8} />
                        <div className="text-xs text-muted mt-0.5">
                          {new Date(block.timestamp * 1000).toLocaleString()}
                        </div>
                      </div>
                    </div>
                    <HashDisplay hash={block.validator} truncate={4} />
                  </Link>
                ))}
                {(blocksData?.blocks ?? []).length === 0 && (
                  <p className="text-muted text-sm text-center py-4">
                    No blocks yet.
                  </p>
                )}
              </div>
            )}
          </Card>

          {/* Pool Stats */}
          <Card title="Transaction Pool">
            {poolData ? (
              <div className="space-y-3">
                <div className="text-2xl font-bold text-accent font-mono">
                  {poolData.count}
                </div>
                <div className="text-xs text-muted">pending transactions</div>
                {poolData.by_type &&
                  Object.entries(poolData.by_type).map(([type, count]) => (
                    <div
                      key={type}
                      className="flex items-center justify-between text-sm"
                    >
                      <Badge label={type} />
                      <span className="font-mono text-foreground">{count}</span>
                    </div>
                  ))}
              </div>
            ) : (
              <Loading size="sm" />
            )}
          </Card>
        </div>
      </div>
    </div>
  );
}
