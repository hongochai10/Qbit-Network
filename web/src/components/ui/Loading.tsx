export function Loading({ size = "md" }: { size?: "sm" | "md" | "lg" }) {
  const sizeMap = { sm: "h-4 w-4", md: "h-8 w-8", lg: "h-12 w-12" };
  return (
    <div className="flex items-center justify-center p-8">
      <div
        className={`${sizeMap[size]} animate-spin rounded-full border-2 border-card-border border-t-accent`}
      />
    </div>
  );
}

export function PageLoading() {
  return (
    <div className="flex items-center justify-center min-h-[400px]">
      <div className="flex flex-col items-center gap-3">
        <div className="h-10 w-10 animate-spin rounded-full border-2 border-card-border border-t-accent" />
        <span className="text-sm text-muted">Loading...</span>
      </div>
    </div>
  );
}
