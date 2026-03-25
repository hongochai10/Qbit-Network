"use client";

import { Header } from "@/components/layout/Header";
import { ValidatorList } from "@/components/validators/ValidatorList";
import { StakeForm } from "@/components/validators/StakeForm";
import { EpochInfo } from "@/components/validators/EpochInfo";
import { Card } from "@/components/ui/Card";

export default function ValidatorsPage() {
  return (
    <div>
      <Header title="Validators & Staking" />
      <div className="p-6 space-y-6">
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          <div className="lg:col-span-2 space-y-6">
            <Card title="Active Validators">
              <ValidatorList />
            </Card>
          </div>
          <div className="space-y-6">
            <EpochInfo />
            <Card title="Stake / Delegate">
              <StakeForm />
            </Card>
          </div>
        </div>
      </div>
    </div>
  );
}
