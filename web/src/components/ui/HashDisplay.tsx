"use client";

import { CopyButton } from "./CopyButton";

interface HashDisplayProps {
  hash: string;
  truncate?: number;
  mono?: boolean;
}

export function HashDisplay({ hash, truncate = 8, mono = true }: HashDisplayProps) {
  if (!hash) return <span className="text-muted">--</span>;

  const display =
    hash.length > truncate * 2 + 2
      ? `${hash.slice(0, truncate)}...${hash.slice(-truncate)}`
      : hash;

  return (
    <span className={`inline-flex items-center gap-1 ${mono ? "font-mono" : ""}`}>
      <span title={hash}>{display}</span>
      <CopyButton text={hash} />
    </span>
  );
}
