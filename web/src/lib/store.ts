import { create } from "zustand";
import { persist } from "zustand/middleware";
import type { NodeInfo, BlockHeader, Transaction } from "./types";

interface AppState {
  // Connection
  apiUrl: string;
  authToken: string;
  connected: boolean;

  // Node info
  nodeInfo: NodeInfo | null;
  chainHeight: number;
  pendingTxs: number;
  peerCount: number;
  validatorCount: number;
  currentEpoch: number;

  // Live feeds
  recentBlocks: BlockHeader[];
  recentTxs: Transaction[];

  // Toast
  toasts: Array<{ id: string; message: string; type: "success" | "error" | "info" }>;

  // Actions
  setConnection: (url: string, token: string) => void;
  setConnected: (connected: boolean) => void;
  setNodeInfo: (info: NodeInfo) => void;
  updateStats: (stats: Partial<AppState>) => void;
  addRecentBlock: (block: BlockHeader) => void;
  addRecentTx: (tx: Transaction) => void;
  addToast: (message: string, type: "success" | "error" | "info") => void;
  removeToast: (id: string) => void;
}

export const useAppStore = create<AppState>()(
  persist(
    (set) => ({
      apiUrl: "",
      authToken: "",
      connected: false,
      nodeInfo: null,
      chainHeight: 0,
      pendingTxs: 0,
      peerCount: 0,
      validatorCount: 0,
      currentEpoch: 0,
      recentBlocks: [],
      recentTxs: [],
      toasts: [],

      setConnection: (url, token) =>
        set(() => {
          if (typeof window !== "undefined") {
            localStorage.setItem("qbit_api_url", url);
            localStorage.setItem("qbit_auth_token", token);
          }
          return { apiUrl: url, authToken: token };
        }),

      setConnected: (connected) => set({ connected }),

      setNodeInfo: (info) =>
        set({
          nodeInfo: info,
          chainHeight: info.chain_height,
          pendingTxs: info.pending_txs,
          peerCount: info.peers,
          validatorCount: info.validators?.length ?? 0,
        }),

      updateStats: (stats) => set(stats),

      addRecentBlock: (block) =>
        set((state) => ({
          recentBlocks: [block, ...state.recentBlocks].slice(0, 20),
          chainHeight: Math.max(state.chainHeight, block.index),
        })),

      addRecentTx: (tx) =>
        set((state) => ({
          recentTxs: [tx, ...state.recentTxs].slice(0, 50),
        })),

      addToast: (message, type) =>
        set((state) => ({
          toasts: [
            ...state.toasts,
            { id: crypto.randomUUID(), message, type },
          ],
        })),

      removeToast: (id) =>
        set((state) => ({
          toasts: state.toasts.filter((t) => t.id !== id),
        })),
    }),
    {
      name: "qbit-store",
      partialize: (state) => ({
        apiUrl: state.apiUrl,
        authToken: state.authToken,
      }),
    }
  )
);
