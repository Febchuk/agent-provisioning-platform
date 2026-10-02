"use client";

import { use, useEffect, useState } from "react";
import { api, ApiError, AgentDetail, Proposal } from "@/app/lib/api";
import { OwnerHeader } from "@/app/components/OwnerHeader";
import { ErrorBanner, Spinner } from "@/app/components/Loading";
import { PassFailBadge } from "@/app/components/PassFail";

export default function ImprovePage({ params }: { params: Promise<{ id: string }> }) {
  const { id: agentId } = use(params);

  const [agent, setAgent] = useState<AgentDetail | null>(null);
  const [agentError, setAgentError] = useState<string | null>(null);

  const [proposal, setProposal] = useState<Proposal | null>(null);
  const [proposing, setProposing] = useState(false);
  const [proposeError, setProposeError] = useState<string | null>(null);

  const [note, setNote] = useState("");
  const [actionError, setActionError] = useState<string | null>(null);
  const [actionLoading, setActionLoading] = useState<"accept" | "deploy" | "reject" | null>(null);

  useEffect(() => {
    api.getAgent(agentId).then(setAgent).catch((e: unknown) => setAgentError(e instanceof ApiError ? e.message : "Failed to load agent"));
  }, [agentId]);

  // UI-R4: poll the proposal every 1.5s while running. Note: the backend's
  // POST /agents/{id}/proposals currently runs the pipeline inline (awaited)
  // rather than backgrounding it, so by the time the request resolves the
  // proposal is already ready/failed -- there's no "running" window to
  // observe client-side today. We still poll here in case that changes, and
  // because it costs nothing when the first fetch already shows a terminal
  // status. See DECISIONS.md.
  useEffect(() => {
    if (!proposal || proposal.status !== "running") return;
    const t = setInterval(() => {
      api
        .getProposal(proposal.id)
        .then(setProposal)
        .catch((e: unknown) => setProposeError(e instanceof ApiError ? e.message : "Failed to poll proposal"));
    }, 1500);
    return () => clearInterval(t);
  }, [proposal]);

  async function handlePropose() {
    setProposing(true);
    setProposeError(null);
    setProposal(null);
    try {
      const { proposal_id } = await api.createProposal(agentId);
      const p = await api.getProposal(proposal_id);
      setProposal(p);
    } catch (e: unknown) {
      setProposeError(e instanceof ApiError ? e.message : "Failed to propose improvement");
    } finally {
      setProposing(false);
    }
  }

  const meetsPolicy = proposal?.verdict?.meets_policy ?? null;
  const requiresNote = meetsPolicy === false;
  const noteFilled = note.trim().length > 0;

  async function handleAccept(deploy: boolean) {
    if (!proposal) return;
    if (requiresNote && !noteFilled) {
      setActionError("A change note is required to accept a proposal that does not meet policy.");
      return;
    }
    setActionLoading(deploy ? "deploy" : "accept");
    setActionError(null);
    try {
      const updated = await api.acceptProposal(proposal.id, deploy, note.trim() || undefined);
      setProposal(updated);
    } catch (e: unknown) {
      setActionError(e instanceof ApiError ? e.message : "Failed to accept proposal");
    } finally {
      setActionLoading(null);
    }
  }

  async function handleReject() {
    if (!proposal) return;
    setActionLoading("reject");
    setActionError(null);
    try {
      const updated = await api.rejectProposal(proposal.id);
      setProposal(updated);
    } catch (e: unknown) {
      setActionError(e instanceof ApiError ? e.message : "Failed to reject proposal");
    } finally {
      setActionLoading(null);
    }
  }

  return (
    <div className="mx-auto max-w-5xl p-4">
      <OwnerHeader agentId={agentId} agentName={agent?.name} />
      {agentError ? (
        <div className="mb-4">
          <ErrorBanner message={agentError} />
        </div>
      ) : null}

      <div className="mb-4">
        <button
          className="rounded bg-black px-3 py-1.5 text-sm font-medium text-white disabled:opacity-40"
          disabled={proposing || proposal?.status === "running" || !agent?.deployed_version_id}
          onClick={() => void handlePropose()}
        >
          {proposing || proposal?.status === "running" ? (
            <Spinner label="Proposing improvement..." />
          ) : (
            "Propose improvement"
          )}
        </button>
        {proposeError ? (
          <div className="mt-2">
            <ErrorBanner message={proposeError} />
          </div>
        ) : null}
      </div>

      {proposal ? (
        <div className="space-y-6">
          {proposal.status === "running" ? (
            <Spinner label="Diagnosing, drafting ops, running evals..." />
          ) : null}

          {proposal.status === "failed" ? (
            <ErrorBanner message="Proposal pipeline failed. See server logs for details." />
          ) : null}

          {proposal.status !== "running" ? (
            <>
              <ScoreStrip proposal={proposal} />
              {proposal.verdict ? <VerdictBanner verdict={proposal.verdict} /> : (
                <p className="text-sm text-gray-500">No candidate was produced (no ops, or all ops were lint-rejected).</p>
              )}
              <GuidelineDiff proposal={proposal} />
              <Reasoning proposal={proposal} />
              {proposal.verdict ? <CaseMatrix verdict={proposal.verdict} /> : null}

              {(proposal.status === "ready") ? (
                <div className="space-y-2 rounded border p-3">
                  <label className="block text-sm">
                    <span className="text-gray-600">Change note{requiresNote ? " (required)" : ""}</span>
                    <textarea
                      className="mt-1 w-full rounded border px-2 py-1 text-sm"
                      rows={2}
                      value={note}
                      onChange={(e) => setNote(e.target.value)}
                      placeholder={requiresNote ? "Required: explain why this is safe to accept anyway" : "Optional note"}
                    />
                  </label>
                  {actionError ? <ErrorBanner message={actionError} /> : null}
                  <div className="flex gap-2">
                    <button
                      className="rounded border px-3 py-1.5 text-sm font-medium disabled:opacity-40"
                      disabled={actionLoading !== null || (requiresNote && !noteFilled)}
                      onClick={() => void handleAccept(false)}
                    >
                      {actionLoading === "accept" ? <Spinner /> : requiresNote ? "Accept anyway" : "Accept"}
                    </button>
                    <button
                      className="rounded bg-black px-3 py-1.5 text-sm font-medium text-white disabled:opacity-40"
                      disabled={actionLoading !== null || (requiresNote && !noteFilled) || !proposal.candidate_version_id}
                      onClick={() => void handleAccept(true)}
                    >
                      {actionLoading === "deploy" ? <Spinner /> : requiresNote ? "Accept anyway & deploy" : "Accept & deploy"}
                    </button>
                    <button
                      className="rounded border px-3 py-1.5 text-sm font-medium text-red-700 disabled:opacity-40"
                      disabled={actionLoading !== null}
                      onClick={() => void handleReject()}
                    >
                      {actionLoading === "reject" ? <Spinner /> : "Reject"}
                    </button>
                  </div>
                </div>
              ) : (
                <div className="rounded border bg-gray-50 p-3 text-sm text-gray-600">
                  Proposal status: <span className="font-medium">{proposal.status}</span>
                  {proposal.decision_note ? <div className="mt-1">Note: {proposal.decision_note}</div> : null}
                </div>
              )}
            </>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

function ScoreStrip({ proposal }: { proposal: Proposal }) {
  const base = proposal.base_eval_run;
  const cand = proposal.cand_eval_run;
  const baseScore = proposal.verdict?.base_score;
  const candScore = proposal.verdict?.cand_score;
  return (
    <div className="flex gap-6 rounded border p-3 text-sm">
      <div>
        <div className="text-gray-500">Base version score</div>
        <div className="text-xl font-semibold">
          {baseScore !== undefined ? `${baseScore.toFixed(1)}%` : "--"}
        </div>
        {base ? <div className="text-xs text-gray-400">eval run {base.id}</div> : null}
      </div>
      <div>
        <div className="text-gray-500">Candidate score</div>
        <div className="text-xl font-semibold">
          {candScore !== undefined ? `${candScore.toFixed(1)}%` : "--"}
        </div>
        {cand ? <div className="text-xs text-gray-400">eval run {cand.id}</div> : null}
      </div>
      {proposal.verdict ? (
        <div>
          <div className="text-gray-500">Avg delta</div>
          <div className="text-xl font-semibold">{proposal.verdict.avg_delta.toFixed(1)} pts</div>
        </div>
      ) : null}
    </div>
  );
}

function VerdictBanner({ verdict }: { verdict: NonNullable<Proposal["verdict"]> }) {
  return (
    <div
      className={
        "rounded border p-3 " + (verdict.meets_policy ? "border-green-300 bg-green-50" : "border-red-300 bg-red-50")
      }
    >
      <PassFailBadge
        passed={verdict.meets_policy}
        trueLabel="Meets policy -- safe to accept"
        falseLabel="Does not meet policy"
      />
      {verdict.reasons.length > 0 ? (
        <ul className="mt-2 list-inside list-disc text-sm text-gray-700">
          {verdict.reasons.map((r, i) => (
            <li key={i}>{r}</li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

function GuidelineDiff({ proposal }: { proposal: Proposal }) {
  return (
    <div className="rounded border p-3">
      <h3 className="mb-2 text-sm font-semibold">Guideline diff</h3>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <div>
          <div className="mb-1 text-xs font-medium text-gray-500">Before</div>
          <pre className="max-h-64 overflow-auto whitespace-pre-wrap rounded bg-gray-50 p-2 text-xs">
            {proposal.diff.before_text || "(empty)"}
          </pre>
        </div>
        <div>
          <div className="mb-1 text-xs font-medium text-gray-500">After</div>
          <pre className="max-h-64 overflow-auto whitespace-pre-wrap rounded bg-gray-50 p-2 text-xs">
            {proposal.diff.after_text || "(empty)"}
          </pre>
        </div>
      </div>
      {(() => {
        const dropped = (proposal.lint ?? []).filter((l) => l.passed === false);
        if (dropped.length === 0) return null;
        return (
          <div className="mt-3">
            <div className="mb-1 text-xs font-medium text-gray-500">
              Lint-dropped ops ({dropped.length} of {proposal.lint?.length ?? 0})
            </div>
            <ul className="list-inside list-disc text-xs text-amber-800">
              {dropped.map((l, i) => (
                <li key={i}>
                  op #{(l.op_index as number) ?? i}
                  {l.reason ? `: ${l.reason}` : ""}
                  {Array.isArray(l.violations) && l.violations.length > 0
                    ? ` (${JSON.stringify(l.violations)})`
                    : ""}
                </li>
              ))}
            </ul>
          </div>
        );
      })()}
      {proposal.skipped ? (
        <div className="mt-3 text-xs text-gray-500">
          Skipped (flaky) cases: {JSON.stringify(proposal.skipped)}
        </div>
      ) : null}
    </div>
  );
}

function Reasoning({ proposal }: { proposal: Proposal }) {
  if (!proposal.diagnoses) return null;
  return (
    <div className="rounded border p-3">
      <h3 className="mb-2 text-sm font-semibold">Improver reasoning</h3>
      <pre className="max-h-64 overflow-auto whitespace-pre-wrap rounded bg-gray-50 p-2 text-xs">
        {JSON.stringify(proposal.diagnoses, null, 2)}
      </pre>
      {proposal.ops && proposal.ops.length > 0 ? (
        <div className="mt-2">
          <div className="mb-1 text-xs font-medium text-gray-500">Proposed ops</div>
          <ul className="space-y-1 text-xs">
            {proposal.ops.map((op, i) => (
              <li key={i} className="rounded bg-gray-50 p-2">
                <span className="font-mono font-medium">{op.op}</span>
                {op.rule_id ? ` ${op.rule_id}` : ""}
                {op.text ? <div className="mt-1 text-gray-700">{op.text}</div> : null}
                {op.why ? <div className="mt-1 text-gray-500">why: {op.why}</div> : null}
                {op.addresses && op.addresses.length > 0 ? (
                  <div className="mt-1 text-gray-500">addresses: {op.addresses.join(", ")}</div>
                ) : null}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}

function CaseMatrix({ verdict }: { verdict: NonNullable<Proposal["verdict"]> }) {
  return (
    <div className="rounded border p-3">
      <h3 className="mb-2 text-sm font-semibold">Case-by-case results</h3>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
        <CaseGroup title="Fixed" cases={verdict.fixed} tone="green" />
        <CaseGroup title="Regressed" cases={verdict.regressed} tone="red" />
        <CaseGroup title="Unchanged" cases={verdict.unchanged} tone="gray" />
      </div>
      {verdict.generalization.total > 0 ? (
        <div className="mt-3 text-xs text-gray-500">
          Generalization (hidden cases): base {verdict.generalization.base} / cand{" "}
          {verdict.generalization.cand} of {verdict.generalization.total}
        </div>
      ) : null}
    </div>
  );
}

function CaseGroup({
  title,
  cases,
  tone,
}: {
  title: string;
  cases: { id: string; name: string; axis: string; pinned: boolean }[];
  tone: "green" | "red" | "gray";
}) {
  const toneClass =
    tone === "green" ? "text-green-700" : tone === "red" ? "text-red-700" : "text-gray-600";
  return (
    <div>
      <div className={"mb-1 text-xs font-semibold " + toneClass}>
        {title} ({cases.length})
      </div>
      {cases.length === 0 ? (
        <div className="text-xs text-gray-400">none</div>
      ) : (
        <ul className="space-y-1 text-xs">
          {cases.map((c) => (
            <li key={c.id}>
              {c.name} <span className="text-gray-400">({c.axis}{c.pinned ? ", pinned" : ""})</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
