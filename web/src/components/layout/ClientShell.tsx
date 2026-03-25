"use client";

import { useEffect, useRef } from "react";
import { Sidebar } from "./Sidebar";
import { ToastContainer } from "@/components/ui/Toast";
import { useAppStore } from "@/lib/store";
import { getApi } from "@/lib/api";

export function ClientShell({ children }: { children: React.ReactNode }) {
  const { apiUrl, authToken, setConnected, setNodeInfo } = useAppStore();
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    const api = getApi();
    api.setConnection(apiUrl, authToken);

    const poll = async () => {
      try {
        const info = await api.getInfo();
        setNodeInfo(info);
        setConnected(true);
      } catch {
        setConnected(false);
      }
    };

    poll();
    intervalRef.current = setInterval(poll, 5000);
    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current);
    };
  }, [apiUrl, authToken, setConnected, setNodeInfo]);

  return (
    <div className="flex min-h-screen">
      <Sidebar />
      <main className="flex-1 ml-[280px]">{children}</main>
      <ToastContainer />
    </div>
  );
}
