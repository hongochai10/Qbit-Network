"use client";

import { use } from "react";
import { Header } from "@/components/layout/Header";
import { BlockDetail } from "@/components/blocks/BlockDetail";

export default function BlockDetailPage({
  params,
}: {
  params: Promise<{ index: string }>;
}) {
  const { index } = use(params);
  const blockIndex = parseInt(index, 10);

  if (isNaN(blockIndex)) {
    return (
      <div>
        <Header title="Block Detail" />
        <div className="p-6 text-error">Invalid block index.</div>
      </div>
    );
  }

  return (
    <div>
      <Header title={`Block #${blockIndex}`} />
      <div className="p-6">
        <BlockDetail index={blockIndex} />
      </div>
    </div>
  );
}
