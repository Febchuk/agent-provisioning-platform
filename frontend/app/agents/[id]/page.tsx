"use client";

import { use, useEffect, useState } from "react";
import { api, ApiError, AgentDetail, AgentVersion } from "@/app/lib/api";
import { OwnerHeader } from "@/app/components/OwnerHeader";
import { ErrorBanner, Spinner } from "@/app/components/Loading";
import { TOOL_NAMES } from "@/app/lib/constants";

export default function StudioPage({ params }: { params: Promise<{ id: string }> }) {
  const { id: agentId } = use(params);

  const [agent, setAgent] = useState<AgentDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  const [tools, setTools] = useState<string[]>([]);
  const [maxSteps, setMaxSteps] = useState<number>(10);
  const [toolTimeoutS, setToolTimeoutS] = useState<number>(30);
  const [changeNote, setChangeNote] = useState("");

  const [saving, setSaving] = useState<"save" | "deploy" | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saveSuccess, setSaveSuccess] = useState<string | null>(null);

  function loadFromVersion(v: AgentVersion) {
    setTools(v.tools);
    setMaxSteps(v.max_steps);
    setToolTimeoutS(v.tool_timeout_s);
  }

  useEffect(() => {
    api
      .getAgent(agentId)
      .then((a) => {
        setAgent(a);
        const deployed = a.versions.find((v) => v.id === a.deployed_version_id) ?? a.versions[0];
        if (deployed) loadFromVersion(deployed);
      })
      .catch((e: unknown) => setError(e instanceof ApiError ? e.message : "Failed to load agent"));
  }, [agentId]);

  const deployedVersion = agent?.versions.find((v) => v.id === agent.deployed_version_id) ?? null;

  function toggleTool(tool: string) {
    setTools((prev) => (prev.includes(tool) ? prev.filter((t) => t !== tool) : [...prev, tool]));
  }

  async function handleSave(deploy: boolean) {
    if (!agent) return;
    setSaving(deploy ? "deploy" : "save");
    setSaveError(null);
    setSaveSuccess(null);
    try {
      const version = await api.createVersion(agent.id, {
        tools,
        max_steps: maxSteps,
        tool_timeout_s: toolTimeoutS,
        change_note: changeNote || (deploy ? "Edited via Studio, deployed" : "Edited via Studio"),
      });
      if (deploy) {
        await api.deploy(agent.id, version.id);
      }
      const refreshed = await api.getAgent(agent.id);
      setAgent(refreshed);
      setSaveSuccess(deploy ? `Saved as v${version.number} and deployed.` : `Saved as v${version.number}.`);
      setChangeNote("");
    } catch (e: unknown) {
      setSaveError(e instanceof ApiError ? e.message : "Failed to save version");
    } finally {
      setSaving(null);
    }
  }

  return (
    <div className="mx-auto max-w-4xl p-4">
      <OwnerHeader agentId={agentId} agentName={agent?.name} />
      {error ? <ErrorBanner message={error} /> : null}

      {!agent ? (
        <Spinner label="Loading agent..." />
      ) : (
        <div className="space-y-6">
          <section className="rounded border p-3">
            <h2 className="mb-2 text-sm font-semibold text-gray-600">Overview</h2>
            <div className="grid grid-cols-2 gap-3 text-sm">
              <div>
                <div className="text-gray-500">Name</div>
                <div className="font-medium">{agent.name}</div>
              </div>
              <div>
                <div className="text-gray-500">Model (read-only)</div>
                <div className="font-mono">{deployedVersion?.model ?? "(server default)"}</div>
              </div>
              <div>
                <div className="text-gray-500">Live version</div>
                <div className="font-medium">v{deployedVersion?.number ?? "--"}</div>
              </div>
            </div>
          </section>

          <section className="rounded border p-3">
            <h2 className="mb-2 text-sm font-semibold text-gray-600">System prompt (read-only)</h2>
            <pre className="max-h-48 overflow-auto whitespace-pre-wrap rounded bg-gray-50 p-2 text-xs">
              {deployedVersion?.system_prompt || "(empty)"}
            </pre>
          </section>

          <section className="rounded border p-3">
            <h2 className="mb-2 text-sm font-semibold text-gray-600">Learned guidelines (read-only view)</h2>
            {deployedVersion && deployedVersion.guidelines.length > 0 ? (
              <ul className="space-y-1 text-sm">
                {deployedVersion.guidelines.map((g) => (
                  <li
                    key={g.id}
                    className="rounded bg-gray-50 px-2 py-1"
                    title={g.addresses && g.addresses.length > 0 ? `added for case ${g.addresses.join(", ")}` : undefined}
                  >
                    {g.text}
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-sm text-gray-400">No guidelines yet.</p>
            )}
            <p className="mt-2 text-xs text-gray-400">
              Guidelines are normally edited by the Improve pipeline. Raw editing here is intentionally
              limited to tools/limits; a full guideline editor is out of scope for this phase (see
              DECISIONS.md).
            </p>
          </section>

          <section className="rounded border p-3">
            <h2 className="mb-2 text-sm font-semibold text-gray-600">Tools</h2>
            <div className="flex flex-wrap gap-3 text-sm">
              {TOOL_NAMES.map((tool) => (
                <label key={tool} className="flex items-center gap-1">
                  <input type="checkbox" checked={tools.includes(tool)} onChange={() => toggleTool(tool)} />
                  <span className="font-mono">{tool}</span>
                </label>
              ))}
            </div>
          </section>

          <section className="rounded border p-3">
            <h2 className="mb-2 text-sm font-semibold text-gray-600">Files</h2>
            {deployedVersion && deployedVersion.files.length > 0 ? (
              <ul className="text-sm">
                {deployedVersion.files.map((f) => (
                  <li key={f.name} className="font-mono">
                    {f.name} {f.size !== undefined ? `(${f.size}b)` : ""}
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-sm text-gray-400">No files.</p>
            )}
          </section>

          <section className="rounded border p-3">
            <h2 className="mb-2 text-sm font-semibold text-gray-600">Limits</h2>
            <div className="flex gap-4">
              <label className="text-sm">
                <span className="block text-gray-600">Max steps</span>
                <input
                  type="number"
                  className="mt-1 w-24 rounded border px-2 py-1"
                  value={maxSteps}
                  onChange={(e) => setMaxSteps(Number(e.target.value))}
                />
              </label>
              <label className="text-sm">
                <span className="block text-gray-600">Tool timeout (s)</span>
                <input
                  type="number"
                  className="mt-1 w-24 rounded border px-2 py-1"
                  value={toolTimeoutS}
                  onChange={(e) => setToolTimeoutS(Number(e.target.value))}
                />
              </label>
            </div>
          </section>

          <section className="rounded border p-3">
            <h2 className="mb-2 text-sm font-semibold text-gray-600">Save new version</h2>
            <label className="block text-sm">
              <span className="text-gray-600">Change note</span>
              <input
                className="mt-1 w-full rounded border px-2 py-1"
                value={changeNote}
                onChange={(e) => setChangeNote(e.target.value)}
                placeholder="What changed and why"
              />
            </label>
            {saveError ? (
              <div className="mt-2">
                <ErrorBanner message={saveError} />
              </div>
            ) : null}
            {saveSuccess ? <p className="mt-2 text-sm text-green-700">{saveSuccess}</p> : null}
            <div className="mt-2 flex gap-2">
              <button
                className="rounded border px-3 py-1.5 text-sm font-medium disabled:opacity-40"
                disabled={saving !== null}
                onClick={() => void handleSave(false)}
              >
                {saving === "save" ? <Spinner label="Saving..." /> : "Save as new version"}
              </button>
              <button
                className="rounded bg-black px-3 py-1.5 text-sm font-medium text-white disabled:opacity-40"
                disabled={saving !== null}
                onClick={() => void handleSave(true)}
              >
                {saving === "deploy" ? <Spinner label="Saving & deploying..." /> : "Save & deploy"}
              </button>
            </div>
          </section>

        </div>
      )}
    </div>
  );
}
