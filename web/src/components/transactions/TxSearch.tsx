"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Search } from "lucide-react";

export function TxSearch() {
  const [query, setQuery] = useState("");
  const router = useRouter();

  const handleSearch = (e: React.FormEvent) => {
    e.preventDefault();
    const q = query.trim();
    if (!q) return;
    router.push(`/transactions/${encodeURIComponent(q)}`);
  };

  return (
    <form onSubmit={handleSearch} className="flex gap-2">
      <div className="relative flex-1">
        <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-muted" />
        <input
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search by TX ID..."
          className="w-full bg-primary border border-card-border rounded-lg py-2.5 pl-10 pr-4 text-sm text-foreground placeholder:text-muted focus:outline-none focus:border-accent transition-colors"
        />
      </div>
      <button
        type="submit"
        className="px-4 py-2.5 bg-accent text-primary rounded-lg text-sm font-medium hover:bg-accent/90 transition-colors"
      >
        Search
      </button>
    </form>
  );
}
