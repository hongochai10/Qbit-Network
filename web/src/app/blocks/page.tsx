"use client";

import { Header } from "@/components/layout/Header";
import { BlockList } from "@/components/blocks/BlockList";
import { Card } from "@/components/ui/Card";

export default function BlocksPage() {
  return (
    <div>
      <Header title="Block Explorer" />
      <div className="p-6">
        <Card>
          <BlockList />
        </Card>
      </div>
    </div>
  );
}
