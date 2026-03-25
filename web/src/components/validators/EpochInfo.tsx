"use client";

import { useCallback } from "react";
import { getApi } from "@/lib/api";
import { useApi } from "@/hooks/useApi";
import { Card } from "@/components/ui/Card";
import { HashDisplay } from "@/components/ui/HashDisplay";
import { Loading } from "@/components/ui/Loading";

export function EpochInfo() {
  const api = getApi();
  const fetcher = useCallback(() => api.getCurrentEpoch(), [api]);
  const { data, loading, error } = useApi(fetcher);

  if (loading) return <Loading size="sm" />;
  if (error) return <div className="text-error text-sm">Failed to load epoch info</div>;
  if (!data) return null;

  return (
    <Card title={`Epoch #${data.epoch}`}>
      <div className="space-y-3">
        {data.validators.map(([addr, stake], i) => (
          <div
            key={addr}
            className="flex items-center justify-between text-sm"
          >
            <div className="flex items-center gap-2">
              <span className="text-muted w-6">#{i + 1}</span>
              <HashDisplay hash={addr} truncate={8} />
            </div>
            <span className="font-mono text-accent">{stake}</span>
          </div>
        ))}
        {data.validators.length === 0 && (
          <p className="text-muted text-sm">No validators in current epoch.</p>
        )}
      </div>
    </Card>
  );
}
