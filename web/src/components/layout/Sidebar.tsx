"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  LayoutDashboard,
  Blocks,
  ArrowLeftRight,
  Wallet,
  ArrowUpRight,
  FileCheck,
  Shield,
  Settings,
} from "lucide-react";
import { useAppStore } from "@/lib/store";

const nav = [
  { href: "/", label: "Dashboard", icon: LayoutDashboard },
  { href: "/blocks", label: "Blocks", icon: Blocks },
  { href: "/transactions", label: "Transactions", icon: ArrowLeftRight },
  { href: "/wallets", label: "Wallets", icon: Wallet },
  { href: "/transfer", label: "Transfer", icon: ArrowUpRight },
  { href: "/notarize", label: "Notarize", icon: FileCheck },
  { href: "/validators", label: "Validators", icon: Shield },
  { href: "/settings", label: "Settings", icon: Settings },
];

export function Sidebar() {
  const pathname = usePathname();
  const connected = useAppStore((s) => s.connected);

  return (
    <aside className="fixed left-0 top-0 bottom-0 w-[280px] bg-card border-r border-card-border flex flex-col z-40">
      {/* Logo */}
      <div className="p-6 border-b border-card-border">
        <div className="flex items-center gap-3">
          <div className="h-9 w-9 rounded-lg bg-accent/20 flex items-center justify-center">
            <span className="text-accent font-bold text-lg">Q</span>
          </div>
          <div>
            <h1 className="text-base font-bold text-foreground">QBit Network</h1>
            <p className="text-xs text-muted">PQC Blockchain Explorer</p>
          </div>
        </div>
      </div>

      {/* Navigation */}
      <nav className="flex-1 p-4 space-y-1">
        {nav.map(({ href, label, icon: Icon }) => {
          const active =
            href === "/" ? pathname === "/" : pathname.startsWith(href);
          return (
            <Link
              key={href}
              href={href}
              className={`flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm transition-colors ${
                active
                  ? "bg-accent/10 text-accent font-medium"
                  : "text-muted hover:text-foreground hover:bg-white/5"
              }`}
            >
              <Icon size={18} />
              {label}
            </Link>
          );
        })}
      </nav>

      {/* Connection status */}
      <div className="p-4 border-t border-card-border">
        <div className="flex items-center gap-2 text-xs">
          <div
            className={`h-2 w-2 rounded-full ${
              connected ? "bg-success" : "bg-error"
            }`}
          />
          <span className="text-muted">
            {connected ? "Connected" : "Disconnected"}
          </span>
        </div>
      </div>
    </aside>
  );
}
