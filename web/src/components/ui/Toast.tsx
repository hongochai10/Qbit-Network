"use client";

import { useEffect } from "react";
import { X, CheckCircle, AlertCircle, Info } from "lucide-react";
import { useAppStore } from "@/lib/store";

const icons = {
  success: CheckCircle,
  error: AlertCircle,
  info: Info,
};

const colors = {
  success: "border-success/50 text-success",
  error: "border-error/50 text-error",
  info: "border-accent/50 text-accent",
};

export function ToastContainer() {
  const toasts = useAppStore((s) => s.toasts);
  const removeToast = useAppStore((s) => s.removeToast);

  return (
    <div className="fixed bottom-4 right-4 z-50 flex flex-col gap-2">
      {toasts.map((toast) => (
        <ToastItem
          key={toast.id}
          id={toast.id}
          message={toast.message}
          type={toast.type}
          onDismiss={removeToast}
        />
      ))}
    </div>
  );
}

function ToastItem({
  id,
  message,
  type,
  onDismiss,
}: {
  id: string;
  message: string;
  type: "success" | "error" | "info";
  onDismiss: (id: string) => void;
}) {
  const Icon = icons[type];

  useEffect(() => {
    const timer = setTimeout(() => onDismiss(id), 5000);
    return () => clearTimeout(timer);
  }, [id, onDismiss]);

  return (
    <div
      className={`flex items-center gap-3 bg-card border ${colors[type]} rounded-lg px-4 py-3 shadow-lg min-w-[300px] animate-slide-in`}
    >
      <Icon size={18} />
      <span className="flex-1 text-sm text-foreground">{message}</span>
      <button onClick={() => onDismiss(id)} className="text-muted hover:text-foreground">
        <X size={14} />
      </button>
    </div>
  );
}
