"use client";

import { useState, useCallback } from "react";
import Link from "next/link";
import { getApi } from "@/lib/api";
import { useApi } from "@/hooks/useApi";
import { HashDisplay } from "@/components/ui/HashDisplay";
import { Loading } from "@/components/ui/Loading";
import { ChevronLeft, ChevronRight } from "lucide-react";

export function BlockList() {
  const [page, setPage] = useState(1);
  const limit = 20;
  const api = getApi();

  const fetcher = useCallback(() => api.getBlocks(page, limit), [api, page]);
  const { data, loading, error } = useApi(fetcher, [page]);

  if (loading) return <Loading />;
  if (error) return <div className="text-error p-4">Error: {error}</div>;
  if (!data) return null;

  const totalPages = Math.ceil(data.total / limit);

  return (
    <div>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-card-border text-left text-muted">
              <th className="py-3 px-4 font-medium">Index</th>
              <th className="py-3 px-4 font-medium">Hash</th>
              <th className="py-3 px-4 font-medium">Validator</th>
              <th className="py-3 px-4 font-medium">TXs</th>
              <th className="py-3 px-4 font-medium">Time</th>
            </tr>
          </thead>
          <tbody>
            {data.blocks.map((block) => (
              <tr
                key={block.index}
                className="border-b border-card-border/50 hover:bg-white/5 transition-colors"
              >
                <td className="py-3 px-4">
                  <Link
                    href={`/blocks/${block.index}`}
                    className="text-accent hover:underline font-mono"
                  >
                    #{block.index}
                  </Link>
                </td>
                <td className="py-3 px-4">
                  <HashDisplay hash={block.hash} />
                </td>
                <td className="py-3 px-4">
                  <HashDisplay hash={block.validator} truncate={6} />
                </td>
                <td className="py-3 px-4 font-mono text-muted">
                  {/* tx count not in header; shown as -- */}
                  --
                </td>
                <td className="py-3 px-4 text-muted">
                  {new Date(block.timestamp * 1000).toLocaleString()}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Pagination */}
      <div className="flex items-center justify-between pt-4 px-4">
        <span className="text-sm text-muted">
          Page {page} of {totalPages} ({data.total} blocks)
        </span>
        <div className="flex items-center gap-2">
          <button
            onClick={() => setPage((p) => Math.max(1, p - 1))}
            disabled={page <= 1}
            className="p-2 rounded border border-card-border text-muted hover:text-foreground disabled:opacity-30 disabled:cursor-not-allowed"
          >
            <ChevronLeft size={16} />
          </button>
          <button
            onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
            disabled={page >= totalPages}
            className="p-2 rounded border border-card-border text-muted hover:text-foreground disabled:opacity-30 disabled:cursor-not-allowed"
          >
            <ChevronRight size={16} />
          </button>
        </div>
      </div>
    </div>
  );
}
