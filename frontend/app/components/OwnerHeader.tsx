"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { api } from "@/app/lib/api";
import { SandboxBanner } from "@/app/components/Loading";

const TABS = [
  { label: "Configure", suffix: "" },
  { label: "Evals", suffix: "/evals" },
  { label: "Improve", suffix: "/improve" },
  { label: "Versions & Deploy", suffix: "/versions" },
];

export function OwnerHeader({ agentId, agentName }: { agentId: string; agentName?: string }) {
  const pathname = usePathname();
  const [sandboxMode, setSandboxMode] = useState<string | null>(null);

  useEffect(() => {
    api
      .health()
      .then((h) => setSandboxMode(h.sandbox_mode))
      .catch(() => setSandboxMode(null));
  }, []);

  const base = `/agents/${agentId}`;

  return (
    <header className="mb-4 border-b pb-2">
      <div className="flex items-center justify-between">
        <div>
          <Link href="/" className="text-xs text-gray-500 hover:underline">
            &larr; All agents
          </Link>
          <h1 className="text-lg font-semibold">{agentName ?? "Agent"}</h1>
        </div>
      </div>
      <nav className="mt-2 flex gap-4 text-sm">
        {TABS.map((tab) => {
          const href = `${base}${tab.suffix}`;
          const active = pathname === href;
          return (
            <Link
              key={tab.label}
              href={href}
              className={
                "border-b-2 pb-1 " +
                (active ? "border-black font-medium text-black" : "border-transparent text-gray-500 hover:text-black")
              }
            >
              {tab.label}
            </Link>
          );
        })}
      </nav>
      <div className="mt-2">
        <SandboxBanner sandboxMode={sandboxMode} />
      </div>
    </header>
  );
}
