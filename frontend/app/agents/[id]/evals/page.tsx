"use client";

import { use, useCallback, useEffect, useState } from "react";
import {
  api,
  ApiError,
  AgentDetail,
  DraftCase,
  EvalCase,
  EvalRunSummary,
  Feedback,
  Policy,
} from "@/app/lib/api";
import { OwnerHeader } from "@/app/components/OwnerHeader";
import { ErrorBanner, Spinner } from "@/app/components/Loading";
import { PassFailDot } from "@/app/components/PassFail";

export default function EvalsPage({ params }: { params: Promise<{ id: string }> }) {
  const { id: agentId } = use(params);

  const [agent, setAgent] = useState<AgentDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  const [feedback, setFeedback] = useState<Feedback[]>([]);
  const [feedbackLoading, setFeedbackLoading] = useState(true);

  const [cases, setCases] = useState<EvalCase[]>([]);
  const [casesLoading, setCasesLoading] = useState(true);

  const [policy, setPolicy] = useState<Policy | null>(null);
  const [policyError, setPolicyError] = useState<string | null>(null);
  const [savingPolicy, setSavingPolicy] = useState(false);

  const [evalRun, setEvalRun] = useState<EvalRunSummary | null>(null);
  const [runningEval, setRunningEval] = useState(false);
  const [evalError, setEvalError] = useState<string | null>(null);

  const loadAgent = useCallback(() => {
    api.getAgent(agentId).then(setAgent).catch((e: unknown) => setError(e instanceof ApiError ? e.message : "Failed to load agent"));
  }, [agentId]);

  const loadFeedback = useCallback(() => {
    setFeedbackLoading(true);
    api
      .listFeedback(agentId, "new")
      .then(setFeedback)
      .catch((e: unknown) => setError(e instanceof ApiError ? e.message : "Failed to load feedback"))
      .finally(() => setFeedbackLoading(false));
  }, [agentId]);

  const loadCases = useCallback(() => {
    setCasesLoading(true);
    api
      .listCases(agentId)
      .then(setCases)
      .catch((e: unknown) => setError(e instanceof ApiError ? e.message : "Failed to load cases"))
      .finally(() => setCasesLoading(false));
  }, [agentId]);

  useEffect(() => {
    const t = setTimeout(() => {
      loadAgent();
      loadFeedback();
      loadCases();
      api
        .getPolicy(agentId)
        .catch((e: unknown) => setPolicyError(e instanceof ApiError ? e.message : "Failed to load policy"))
        .then((p) => {
          if (p) setPolicy(p);
        });
    }, 0);
    return () => clearTimeout(t);
  }, [agentId, loadAgent, loadFeedback, loadCases]);

  // UI-R4: poll the eval run every 1.5s while in progress.
  useEffect(() => {
    if (!evalRun || evalRun.status !== "running") return;
    const t = setInterval(() => {
      api
        .getEvalRun(evalRun.id)
        .then((r) => {
          setEvalRun(r);
          if (r.status !== "running") loadCases();
        })
        .catch((e: unknown) => setEvalError(e instanceof ApiError ? e.message : "Failed to poll eval run"));
    }, 1500);
    return () => clearInterval(t);
  }, [evalRun, loadCases]);

  async function handleRunEvals() {
    if (!agent?.deployed_version_id) {
      setEvalError("Agent has no deployed version to evaluate");
      return;
    }
    setRunningEval(true);
    setEvalError(null);
    try {
      const run = await api.createEvalRun(agentId, agent.deployed_version_id);
      setEvalRun(run);
    } catch (e: unknown) {
      setEvalError(e instanceof ApiError ? e.message : "Failed to start eval run");
    } finally {
      setRunningEval(false);
    }
  }

  const totalTrials = evalRun ? evalRun.cases.reduce((sum, c) => sum + c.trials.length, 0) : 0;
  const expectedTrials = evalRun ? evalRun.cases.length * evalRun.trials_per_case : 0;
  // Approximate expected trial count across ALL visible cases (not just
  // those with results so far) using the case list length, since a running
  // eval run's `cases` array only grows as trials complete.
  const expectedTotal = cases.length > 0 ? cases.length * (evalRun?.trials_per_case ?? policy?.trials_per_case ?? 3) : expectedTrials;

  return (
    <div className="mx-auto max-w-5xl p-4">
      <OwnerHeader agentId={agentId} agentName={agent?.name} />
      {error ? (
        <div className="mb-4">
          <ErrorBanner message={error} />
        </div>
      ) : null}

      <section className="mb-6">
        <h2 className="mb-2 text-base font-semibold">Feedback inbox</h2>
        {feedbackLoading ? (
          <Spinner label="Loading feedback..." />
        ) : feedback.length === 0 ? (
          <p className="text-sm text-gray-500">No new feedback.</p>
        ) : (
          <div className="space-y-3">
            {feedback.map((f) => (
              <FeedbackCard
                key={f.id}
                feedback={f}
                agentId={agentId}
                onHandled={() => {
                  loadFeedback();
                  loadCases();
                }}
              />
            ))}
          </div>
        )}
      </section>

      <section className="mb-6">
        <div className="mb-2 flex items-center justify-between">
          <h2 className="text-base font-semibold">Eval cases</h2>
          <button
            className="rounded bg-black px-3 py-1.5 text-sm font-medium text-white disabled:opacity-40"
            disabled={runningEval || evalRun?.status === "running" || !agent?.deployed_version_id}
            onClick={() => void handleRunEvals()}
          >
            {runningEval || evalRun?.status === "running" ? <Spinner label="Running evals..." /> : "Run evals"}
          </button>
        </div>
        {evalError ? (
          <div className="mb-2">
            <ErrorBanner message={evalError} />
          </div>
        ) : null}
        {evalRun ? (
          <div className="mb-2 text-sm text-gray-600">
            Eval run status: <span className="font-medium">{evalRun.status}</span>
            {evalRun.status === "running" ? (
              <span>
                {" "}
                &mdash; {totalTrials} / {expectedTotal} trials
              </span>
            ) : null}
          </div>
        ) : null}

        {casesLoading ? (
          <Spinner label="Loading cases..." />
        ) : cases.length === 0 ? (
          <p className="text-sm text-gray-500">No eval cases yet.</p>
        ) : (
          <table className="w-full border-collapse text-sm">
            <thead>
              <tr className="border-b text-left text-gray-500">
                <th className="py-1 pr-2">Name</th>
                <th className="py-1 pr-2">Axis</th>
                <th className="py-1 pr-2">Check</th>
                <th className="py-1 pr-2">Pinned</th>
                <th className="py-1 pr-2">Last 3 trials</th>
              </tr>
            </thead>
            <tbody>
              {cases.map((c) => {
                const runCaseLive = evalRun?.cases.find((rc) => rc.case_id === c.id);
                const trials = runCaseLive
                  ? runCaseLive.trials
                  : c.latest_result
                  ? Array.from({ length: c.latest_result.trials }).map((_, i) => ({
                      trial: i,
                      // We only know aggregate passing count from the
                      // non-live summary, so render that many "pass" dots
                      // followed by "fail" dots -- an approximation flagged
                      // in DECISIONS.md.
                      passed: i < (c.latest_result?.passing ?? 0),
                    }))
                  : [];
                return (
                  <tr key={c.id} className="border-b">
                    <td className="py-2 pr-2">{c.name}</td>
                    <td className="py-2 pr-2">{c.axis}</td>
                    <td className="py-2 pr-2">{c.check_type}</td>
                    <td className="py-2 pr-2">{c.pinned ? "✓" : ""}</td>
                    <td className="py-2 pr-2">
                      <div className="flex gap-1">
                        {trials.length === 0 ? (
                          <span className="text-gray-400">no runs yet</span>
                        ) : (
                          trials.slice(-3).map((t, i) => <PassFailDot key={i} passed={t.passed} />)
                        )}
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </section>

      <section>
        <h2 className="mb-2 text-base font-semibold">Policy</h2>
        {policyError ? (
          <div className="mb-2">
            <ErrorBanner message={policyError} />
          </div>
        ) : null}
        {policy ? (
          <PolicyPanel
            policy={policy}
            saving={savingPolicy}
            onSave={async (next) => {
              setSavingPolicy(true);
              setPolicyError(null);
              try {
                const saved = await api.putPolicy(agentId, next);
                setPolicy(saved);
              } catch (e: unknown) {
                setPolicyError(e instanceof ApiError ? e.message : "Failed to save policy");
              } finally {
                setSavingPolicy(false);
              }
            }}
          />
        ) : (
          <Spinner label="Loading policy..." />
        )}
      </section>
    </div>
  );
}

function FeedbackCard({
  feedback,
  agentId,
  onHandled,
}: {
  feedback: Feedback;
  agentId: string;
  onHandled: () => void;
}) {
  const [draft, setDraft] = useState<DraftCase | null>(null);
  const [drafting, setDrafting] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [dismissing, setDismissing] = useState(false);
  const [localError, setLocalError] = useState<string | null>(null);

  async function handleDraft() {
    setDrafting(true);
    setLocalError(null);
    try {
      const d = await api.draftCase(feedback.id);
      setDraft(d);
    } catch (e: unknown) {
      setLocalError(e instanceof ApiError ? e.message : "Failed to draft case");
    } finally {
      setDrafting(false);
    }
  }

  async function handleConfirm() {
    if (!draft) return;
    setConfirming(true);
    setLocalError(null);
    try {
      await api.createCase(agentId, {
        name: draft.name,
        axis: draft.axis,
        check_type: draft.check_type,
        check_spec: draft.check_spec,
        history: draft.history as unknown[] | undefined,
        from_feedback_id: draft.from_feedback_id,
      });
      onHandled();
    } catch (e: unknown) {
      setLocalError(e instanceof ApiError ? e.message : "Failed to confirm case");
    } finally {
      setConfirming(false);
    }
  }

  async function handleDismiss() {
    setDismissing(true);
    setLocalError(null);
    try {
      await api.dismissFeedback(feedback.id);
      onHandled();
    } catch (e: unknown) {
      setLocalError(e instanceof ApiError ? e.message : "Failed to dismiss feedback");
    } finally {
      setDismissing(false);
    }
  }

  return (
    <div className="rounded border p-3">
      <div className="flex items-center justify-between">
        <div className="text-sm">
          <span className="font-medium">{feedback.rating === "up" ? "\u{1F44D}" : "\u{1F44E}"}</span>{" "}
          {feedback.correction ? (
            <span>&quot;{feedback.correction}&quot;</span>
          ) : (
            <span className="text-gray-400">no correction text</span>
          )}
        </div>
        <div className="flex gap-2">
          {!draft ? (
            <button
              className="rounded border px-2 py-1 text-xs disabled:opacity-40"
              disabled={drafting}
              onClick={() => void handleDraft()}
            >
              {drafting ? <Spinner label="Drafting..." /> : "Draft case"}
            </button>
          ) : null}
          <button
            className="rounded border px-2 py-1 text-xs disabled:opacity-40"
            disabled={dismissing}
            onClick={() => void handleDismiss()}
          >
            {dismissing ? <Spinner label="Dismissing..." /> : "Dismiss"}
          </button>
        </div>
      </div>
      {localError ? (
        <div className="mt-2">
          <ErrorBanner message={localError} />
        </div>
      ) : null}
      {draft ? (
        <div className="mt-2 rounded bg-gray-50 p-2 text-xs">
          <div>
            <span className="font-medium">Name:</span> {draft.name}
          </div>
          <div>
            <span className="font-medium">Axis:</span> {draft.axis}
          </div>
          <div>
            <span className="font-medium">Check type:</span> {draft.check_type}
          </div>
          {draft.check_spec?.rubric ? (
            <div>
              <span className="font-medium">Rubric:</span> {draft.check_spec.rubric}
            </div>
          ) : null}
          <button
            className="mt-2 rounded bg-black px-2 py-1 text-xs text-white disabled:opacity-40"
            disabled={confirming}
            onClick={() => void handleConfirm()}
          >
            {confirming ? <Spinner label="Adding..." /> : "Add as test case"}
          </button>
        </div>
      ) : null}
    </div>
  );
}

function PolicyPanel({
  policy,
  saving,
  onSave,
}: {
  policy: Policy;
  saving: boolean;
  onSave: (next: Partial<Policy>) => void;
}) {
  const [minAvg, setMinAvg] = useState(policy.min_avg_improvement_pct);
  const [trialsPerCase, setTrialsPerCase] = useState(policy.trials_per_case);
  const [passThreshold, setPassThreshold] = useState(policy.pass_threshold);
  const [maxRegressionsText, setMaxRegressionsText] = useState(
    JSON.stringify(policy.max_regressions ?? {})
  );
  const [jsonError, setJsonError] = useState<string | null>(null);

  return (
    <div className="max-w-md space-y-3 rounded border p-3 text-sm">
      <label className="block">
        <span className="text-gray-600">Min avg improvement (%)</span>
        <input
          type="number"
          className="mt-1 w-full rounded border px-2 py-1"
          value={minAvg}
          onChange={(e) => setMinAvg(Number(e.target.value))}
        />
      </label>
      <label className="block">
        <span className="text-gray-600">Trials per case</span>
        <input
          type="number"
          className="mt-1 w-full rounded border px-2 py-1"
          value={trialsPerCase}
          onChange={(e) => setTrialsPerCase(Number(e.target.value))}
        />
      </label>
      <label className="block">
        <span className="text-gray-600">Pass threshold (trials needed to pass)</span>
        <input
          type="number"
          className="mt-1 w-full rounded border px-2 py-1"
          value={passThreshold}
          onChange={(e) => setPassThreshold(Number(e.target.value))}
        />
      </label>
      <label className="block">
        <span className="text-gray-600">Max regressions per axis (JSON)</span>
        <input
          type="text"
          className="mt-1 w-full rounded border px-2 py-1 font-mono"
          value={maxRegressionsText}
          onChange={(e) => setMaxRegressionsText(e.target.value)}
        />
      </label>
      {jsonError ? <ErrorBanner message={jsonError} /> : null}
      <button
        className="rounded bg-black px-3 py-1.5 text-sm font-medium text-white disabled:opacity-40"
        disabled={saving}
        onClick={() => {
          try {
            const maxRegressions = JSON.parse(maxRegressionsText);
            setJsonError(null);
            onSave({
              min_avg_improvement_pct: minAvg,
              trials_per_case: trialsPerCase,
              pass_threshold: passThreshold,
              max_regressions: maxRegressions,
            });
          } catch {
            setJsonError("Max regressions must be valid JSON, e.g. {\"accuracy\": 0}");
          }
        }}
      >
        {saving ? <Spinner label="Saving..." /> : "Save policy"}
      </button>
    </div>
  );
}
