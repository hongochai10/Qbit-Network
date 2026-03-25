"use client";

import { useState } from "react";
import { Header } from "@/components/layout/Header";
import { Card } from "@/components/ui/Card";
import { useAppStore } from "@/lib/store";
import { getApi } from "@/lib/api";
import { Save, Zap, CheckCircle, XCircle } from "lucide-react";

export default function SettingsPage() {
  const { apiUrl, authToken, setConnection, connected } = useAppStore();
  const addToast = useAppStore((s) => s.addToast);
  const [url, setUrl] = useState(apiUrl);
  const [token, setToken] = useState(authToken);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<boolean | null>(null);

  const handleSave = () => {
    setConnection(url, token);
    const api = getApi();
    api.setConnection(url, token);
    addToast("Connection settings saved", "success");
  };

  const handleTest = async () => {
    setTesting(true);
    setTestResult(null);
    try {
      const api = getApi();
      api.setConnection(url, token);
      await api.getHealth();
      setTestResult(true);
      addToast("Connection successful", "success");
    } catch {
      setTestResult(false);
      addToast("Connection failed", "error");
    } finally {
      setTesting(false);
    }
  };

  return (
    <div>
      <Header title="Settings" />
      <div className="p-6 max-w-2xl space-y-6">
        <Card title="API Connection">
          <div className="space-y-4">
            <div>
              <label className="block text-sm text-muted mb-2">
                API Base URL
              </label>
              <input
                type="text"
                value={url}
                onChange={(e) => setUrl(e.target.value)}
                placeholder="http://localhost:8545"
                className="w-full bg-primary border border-card-border rounded-lg py-2.5 px-4 text-sm font-mono text-foreground placeholder:text-muted focus:outline-none focus:border-accent"
              />
            </div>

            <div>
              <label className="block text-sm text-muted mb-2">
                Auth Token (optional)
              </label>
              <input
                type="password"
                value={token}
                onChange={(e) => setToken(e.target.value)}
                placeholder="Bearer token..."
                className="w-full bg-primary border border-card-border rounded-lg py-2.5 px-4 text-sm font-mono text-foreground placeholder:text-muted focus:outline-none focus:border-accent"
              />
            </div>

            <div className="flex items-center gap-3 pt-2">
              <button
                onClick={handleSave}
                className="flex items-center gap-2 px-4 py-2.5 bg-accent text-primary rounded-lg text-sm font-medium hover:bg-accent/90 transition-colors"
              >
                <Save size={16} />
                Save
              </button>
              <button
                onClick={handleTest}
                disabled={testing}
                className="flex items-center gap-2 px-4 py-2.5 border border-card-border rounded-lg text-sm text-muted hover:text-foreground hover:border-accent/50 transition-colors disabled:opacity-50"
              >
                <Zap size={16} />
                {testing ? "Testing..." : "Test Connection"}
              </button>
              {testResult !== null && (
                <span className="flex items-center gap-1 text-sm">
                  {testResult ? (
                    <>
                      <CheckCircle size={16} className="text-success" />
                      <span className="text-success">OK</span>
                    </>
                  ) : (
                    <>
                      <XCircle size={16} className="text-error" />
                      <span className="text-error">Failed</span>
                    </>
                  )}
                </span>
              )}
            </div>
          </div>
        </Card>

        <Card title="Status">
          <div className="flex items-center gap-2 text-sm">
            <div
              className={`h-3 w-3 rounded-full ${
                connected ? "bg-success" : "bg-error"
              }`}
            />
            <span className="text-foreground">
              {connected ? "Connected to node" : "Not connected"}
            </span>
          </div>
        </Card>
      </div>
    </div>
  );
}
