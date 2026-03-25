import type { ReactNode } from "react";

interface CardProps {
  children: ReactNode;
  className?: string;
  title?: string;
}

export function Card({ children, className = "", title }: CardProps) {
  return (
    <div className={`bg-card border border-card-border rounded-lg p-5 ${className}`}>
      {title && (
        <h3 className="text-sm font-semibold text-muted uppercase tracking-wider mb-4">
          {title}
        </h3>
      )}
      {children}
    </div>
  );
}
