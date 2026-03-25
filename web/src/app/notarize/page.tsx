"use client";

import { Header } from "@/components/layout/Header";
import { NotarizeForm } from "@/components/notarize/NotarizeForm";
import { VerifyForm } from "@/components/notarize/VerifyForm";
import { Card } from "@/components/ui/Card";

export default function NotarizePage() {
  return (
    <div>
      <Header title="Notarize & Verify" />
      <div className="p-6 space-y-6">
        <Card title="Notarize Document">
          <NotarizeForm />
        </Card>
        <Card title="Verify Document">
          <VerifyForm />
        </Card>
      </div>
    </div>
  );
}
