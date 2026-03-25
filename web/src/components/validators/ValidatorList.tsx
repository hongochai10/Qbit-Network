"use client";

import { useCallback } from "react";
import { getApi } from "@/lib/api";
import { useApi } from "@/hooks/useApi";
import { HashDisplay } from "@/components/ui/HashDisplay";
import { Loading } from "@/components/ui/Loading";
import { Shield } from "lucide-react";

export function ValidatorList() {
  const api = getApi();
  const fetcher = useCallback(() => api.getValidators(), [api]);
  const { data, loading, error } = useApi(fetcher);

  if (loading) return <Loading />;
  if (error) return <div className="text-error p-4">Error: {error}</div>;

  const validators = data?.validators ?? [];

  return (
    <div className="space-y-3">
      {validators.length === 0 ? (
        <div className="text-center py-12 text-muted">No validators registered.</div>
      ) : (
        validators.map((addr, i) => (
          <div
            key={addr}
            className="flex items-center gap-4 bg-card border border-card-border rounded-lg p-4 hover:border-accent/30 transition-colors"
          >
            <div className="h-10 w-10 rounded-lg bg-purple-500/10 flex items-center justify-center">
              <Shield size={18} className="text-purple-400" />
            </div>
            <div className="flex-1 min-w-0">
              <div className="text-xs text-muted mb-1">Validator #{i + 1}</div>
              <HashDisplay hash={addr} truncate={16} />
            </div>
          </div>
        ))
      )}
    </div>
  );
}
