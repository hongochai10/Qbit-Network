const variants: Record<string, string> = {
  NOTARIZE: "bg-accent/20 text-accent",
  STORE: "bg-purple-500/20 text-purple-400",
  SHARE: "bg-success/20 text-success",
  REGISTER_KEY: "bg-yellow-500/20 text-yellow-400",
  STAKE: "bg-orange-500/20 text-orange-400",
  DELEGATE: "bg-blue-500/20 text-blue-400",
  UNSTAKE: "bg-red-500/20 text-red-400",
  TRANSFER: "bg-emerald-500/20 text-emerald-400",
  COINBASE: "bg-yellow-500/20 text-yellow-400",
  default: "bg-card-border/50 text-muted",
};

export function Badge({ label, variant }: { label: string; variant?: string }) {
  const key = variant || label;
  const cls = variants[key] || variants.default;
  return (
    <span className={`inline-block px-2 py-0.5 rounded text-xs font-medium ${cls}`}>
      {label}
    </span>
  );
}
