"use client";

import { use, useEffect, useState } from "react";
import { api, ApiError, AgentDetail } from "@/app/lib/api";
import { OwnerHeader } from "@/app/components/OwnerHeader";
import { ErrorBanner, Spinner } from "@/app/components/Loading";

export default function VersionsPage({ params }: { params: Promise<{ id: string }> }) {
  const { id: agentId } = use(params);

  const [agent, setAgent] = useState<AgentDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [deploying, setDeploying] = useState<string | null>(null);
  const [deployError, setDeployError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  function load() {
    api.getAgent(agentId).then(setAgent).catch((e: unknown) => setError(e instanceof ApiError ? e.message : "Failed to load agent"));
  }

  useEffect(load, [agentId]);

  async function handleDeploy(versionId: string) {
    if (!agent) return;
    setDeploying(versionId);
    setDeployError(null);
    try {
      await api.deploy(agent.id, versionId);
      load();
    } catch (e: unknown) {
      setDeployError(e instanceof ApiError ? e.message : "Failed to deploy version");
    } finally {
      setDeploying(null);
    }
  }

  const shareUrl = agent ? `${typeof window !== "undefined" ? window.location.origin : ""}/share/${agent.slug}` : "";

  return (
    <div className="mx-auto max-w-4xl p-4">
      <OwnerHeader agentId={agentId} agentName={agent?.name} />
      {error ? <ErrorBanner message={error} /> : null}

      {!agent ? (
        <Spinner label="Loading agent..." />
      ) : (
        <div className="space-y-4">
          <div className="flex items-center gap-2 rounded border bg-gray-50 p-3 text-sm">
            <span className="text-gray-500">Share link:</span>
            <code className="flex-1 truncate">{shareUrl}</code>
            <button
              className="rounded border px-2 py-1 text-xs"
              onClick={() => {
                navigator.clipboard?.writeText(shareUrl).then(() => {
                  setCopied(true);
                  setTimeout(() => setCopied(false), 1500);
                });
              }}
            >
              {copied ? "Copied!" : "Copy"}
            </button>
          </div>

          {deployError ? <ErrorBanner message={deployError} /> : null}

          <table className="w-full border-collapse text-sm">
            <thead>
              <tr className="border-b text-left text-gray-500">
                <th className="py-1 pr-2">Version</th>
                <th className="py-1 pr-2">Source</th>
                <th className="py-1 pr-2">Change note</th>
                <th className="py-1 pr-2">Live</th>
                <th className="py-1 pr-2">Action</th>
              </tr>
            </thead>
            <tbody>
              {agent.versions.map((v) => {
                const isLive = v.id === agent.deployed_version_id;
                return (
                  <tr key={v.id} className="border-b">
                    <td className="py-2 pr-2 font-medium">v{v.number}</td>
                    <td className="py-2 pr-2">{v.source}</td>
                    <td className="py-2 pr-2">{v.change_note || <span className="text-gray-400">--</span>}</td>
                    <td className="py-2 pr-2">
                      {isLive ? (
                        <span className="rounded bg-green-100 px-2 py-0.5 text-xs font-medium text-green-800">
                          &#10003; live
                        </span>
                      ) : null}
                    </td>
                    <td className="py-2 pr-2">
                      {!isLive ? (
                        <button
                          className="rounded border px-2 py-1 text-xs disabled:opacity-40"
                          disabled={deploying !== null}
                          onClick={() => void handleDeploy(v.id)}
                        >
                          {deploying === v.id ? <Spinner label="Deploying..." /> : "Deploy / Roll back"}
                        </button>
                      ) : null}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
