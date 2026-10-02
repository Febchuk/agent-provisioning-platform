"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { api, ApiError, AgentListItem, Template } from "@/app/lib/api";
import { ErrorBanner, SandboxBanner, Spinner } from "@/app/components/Loading";

export default function AgentsPage() {
  const router = useRouter();
  const [agents, setAgents] = useState<AgentListItem[] | null>(null);
  const [agentsError, setAgentsError] = useState<string | null>(null);

  const [templates, setTemplates] = useState<Template[] | null>(null);
  const [templatesError, setTemplatesError] = useState<string | null>(null);

  const [isolated, setIsolated] = useState<boolean | null>(null);

  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [templateId, setTemplateId] = useState<string>("");
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);

  useEffect(() => {
    api.listAgents().then(setAgents).catch((e: unknown) => setAgentsError(e instanceof ApiError ? e.message : "Failed to load agents"));
    api
      .templates()
      .then((ts) => {
        setTemplates(ts);
        if (ts.length > 0) setTemplateId(ts[0].id);
      })
      .catch((e: unknown) => setTemplatesError(e instanceof ApiError ? e.message : "Failed to load templates"));
    api.health().then((h) => setIsolated(h.isolated)).catch(() => setIsolated(null));
  }, []);

  async function handleCreate() {
    if (!name.trim() || !templateId) return;
    setCreating(true);
    setCreateError(null);
    try {
      const agent = await api.createAgent({ name: name.trim(), description: description.trim(), template: templateId });
      router.push(`/agents/${agent.id}`);
    } catch (e: unknown) {
      setCreateError(e instanceof ApiError ? e.message : "Failed to create agent");
    } finally {
      setCreating(false);
    }
  }

  return (
    <div className="mx-auto max-w-5xl p-4">
      <h1 className="mb-4 text-xl font-semibold">Agents</h1>
      <div className="mb-4">
        <SandboxBanner isolated={isolated} />
      </div>

      <section className="mb-8">
        {agentsError ? (
          <ErrorBanner message={agentsError} />
        ) : agents === null ? (
          <Spinner label="Loading agents..." />
        ) : agents.length === 0 ? (
          <p className="text-sm text-gray-500">No agents yet. Create one below.</p>
        ) : (
          <table className="w-full border-collapse text-sm">
            <thead>
              <tr className="border-b text-left text-gray-500">
                <th className="py-1 pr-2">Name</th>
                <th className="py-1 pr-2">Live version</th>
                <th className="py-1 pr-2">Chats (7d)</th>
                <th className="py-1 pr-2">Feedback to review</th>
                <th className="py-1 pr-2">Eval score</th>
              </tr>
            </thead>
            <tbody>
              {agents.map((a) => (
                <tr key={a.id} className="border-b hover:bg-gray-50">
                  <td className="py-2 pr-2">
                    <a className="font-medium text-blue-700 hover:underline" href={`/agents/${a.id}`}>
                      {a.name}
                    </a>
                    {a.description ? <div className="text-xs text-gray-400">{a.description}</div> : null}
                  </td>
                  <td className="py-2 pr-2">{a.deployed_version_number ?? "--"}</td>
                  <td className="py-2 pr-2">{a.chat_count_7d}</td>
                  <td className="py-2 pr-2">{a.new_feedback_count}</td>
                  <td className="py-2 pr-2">{a.latest_eval_score !== null ? `${a.latest_eval_score}%` : "--"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <section className="max-w-md rounded border p-4">
        <h2 className="mb-3 text-base font-semibold">New agent</h2>
        {templatesError ? <ErrorBanner message={templatesError} /> : null}
        {templates === null ? (
          <Spinner label="Loading templates..." />
        ) : (
          <div className="space-y-3">
            <label className="block text-sm">
              <span className="text-gray-600">Name</span>
              <input
                className="mt-1 w-full rounded border px-2 py-1"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="e.g. Revenue Analyst"
              />
            </label>
            <label className="block text-sm">
              <span className="text-gray-600">Description</span>
              <input
                className="mt-1 w-full rounded border px-2 py-1"
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                placeholder="Optional"
              />
            </label>
            <div className="space-y-2">
              <span className="text-sm text-gray-600">Template</span>
              {templates.map((t) => (
                <label key={t.id} className="flex items-start gap-2 rounded border p-2 text-sm">
                  <input
                    type="radio"
                    name="template"
                    className="mt-1"
                    checked={templateId === t.id}
                    onChange={() => setTemplateId(t.id)}
                  />
                  <span>
                    <span className="font-medium">{t.label}</span>
                    <div className="text-xs text-gray-500">{t.description}</div>
                    {t.tools.length > 0 ? (
                      <div className="mt-1 text-xs text-gray-400">tools: {t.tools.join(", ")}</div>
                    ) : null}
                    {t.file_names.length > 0 ? (
                      <div className="text-xs text-gray-400">files: {t.file_names.join(", ")}</div>
                    ) : null}
                  </span>
                </label>
              ))}
            </div>
            {createError ? <ErrorBanner message={createError} /> : null}
            <button
              className="rounded bg-black px-3 py-1.5 text-sm font-medium text-white disabled:opacity-40"
              disabled={creating || !name.trim()}
              onClick={() => void handleCreate()}
            >
              {creating ? <Spinner label="Creating..." /> : "Create → Studio"}
            </button>
          </div>
        )}
      </section>
    </div>
  );
}
