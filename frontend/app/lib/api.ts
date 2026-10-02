// Thin fetch wrapper around the backend (see backend/app/main.py for the
// exact request/response shapes this mirrors). No backend endpoint is
// invented here -- every function below maps 1:1 to a route that exists.

export const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL || "http://127.0.0.1:8000";

export class ApiError extends Error {
  status: number;
  body: unknown;
  constructor(status: number, body: unknown, message?: string) {
    super(message || `Request failed with status ${status}`);
    this.status = status;
    this.body = body;
  }
}

async function request<T>(
  path: string,
  options: RequestInit = {}
): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API_BASE_URL}${path}`, {
      ...options,
      headers: {
        ...(options.body ? { "Content-Type": "application/json" } : {}),
        ...(options.headers || {}),
      },
    });
  } catch {
    throw new ApiError(0, null, "Network error: could not reach the backend");
  }

  if (!res.ok) {
    let body: unknown = null;
    try {
      body = await res.json();
    } catch {
      // ignore
    }
    const detail =
      body && typeof body === "object" && "detail" in body
        ? String((body as { detail: unknown }).detail)
        : `Request failed (${res.status})`;
    throw new ApiError(res.status, body, detail);
  }

  if (res.status === 204) return undefined as T;
  const text = await res.text();
  if (!text) return undefined as T;
  return JSON.parse(text) as T;
}

const get = <T>(path: string) => request<T>(path, { method: "GET" });
const post = <T>(path: string, body?: unknown) =>
  request<T>(path, {
    method: "POST",
    body: body === undefined ? undefined : JSON.stringify(body),
  });
const patch = <T>(path: string, body?: unknown) =>
  request<T>(path, {
    method: "PATCH",
    body: body === undefined ? undefined : JSON.stringify(body),
  });
const put = <T>(path: string, body?: unknown) =>
  request<T>(path, {
    method: "PUT",
    body: body === undefined ? undefined : JSON.stringify(body),
  });

// ---------------------------------------------------------------------------
// Types (mirroring backend/app/main.py response shapes)
// ---------------------------------------------------------------------------
export type Health = { ok: boolean; sandbox_mode: "docker" | "local-unsafe" };

export type Template = {
  id: string;
  label: string;
  description: string;
  tools: string[];
  file_names: string[];
};

export type AgentListItem = {
  id: string;
  slug: string;
  name: string;
  description: string;
  deployed_version_number: number | null;
  chat_count_7d: number;
  new_feedback_count: number;
  latest_eval_score: number | null;
  created_at: string;
};

export type VersionFile = { name: string; size?: number };

export type Guideline = {
  id: string;
  section?: string;
  text: string;
  addresses?: string[];
};

export type AgentVersion = {
  id: string;
  agent_id: string;
  number: number;
  parent_version_id: string | null;
  system_prompt: string;
  guidelines: Guideline[];
  tools: string[];
  model: string;
  max_steps: number;
  tool_timeout_s: number;
  files: VersionFile[];
  source: string;
  change_note: string;
  created_at: string;
};

export type AgentDetail = {
  id: string;
  slug: string;
  name: string;
  description: string;
  deployed_version_id: string | null;
  created_at: string;
  versions: AgentVersion[];
};

export type ShareInfo = {
  name: string;
  description: string;
  deployed_version_number: number | null;
};

export type ConversationMessage = {
  id: string;
  seq: number;
  role: string;
  content: string | null;
  tool_calls: unknown;
  tool_call_id: string | null;
  run_id: string | null;
};

export type RunSummary = {
  id: string;
  status: string;
  steps: unknown;
  final_answer: string | null;
  error: string | null;
  started_at: string;
  finished_at: string | null;
};

export type ConversationDetail = {
  id: string;
  agent_id: string;
  version_id: string;
  channel: string;
  created_at: string;
  messages: ConversationMessage[];
  runs: RunSummary[];
};

export type RunEvent = {
  type: string;
  [key: string]: unknown;
};

export type Feedback = {
  id: string;
  run_id: string;
  conversation_id: string;
  rating: "up" | "down";
  correction: string | null;
  status: string;
  created_at: string;
};

export type DraftCase = {
  name: string;
  axis: string;
  check_type: string;
  check_spec: { rubric?: string; [key: string]: unknown };
  history: unknown;
  from_feedback_id: string;
  status: "draft";
};

export type EvalCaseLatestResult = {
  eval_run_id: string;
  trials: number;
  passing: number;
} | null;

export type EvalCase = {
  id: string;
  name: string;
  axis: string;
  check_type: string;
  check_spec: Record<string, unknown>;
  pinned: boolean;
  status: string;
  from_feedback_id: string | null;
  latest_result: EvalCaseLatestResult;
};

export type Policy = {
  agent_id: string;
  min_avg_improvement_pct: number;
  max_regressions: Record<string, number>;
  trials_per_case: number;
  pass_threshold: number;
};

export type EvalRunCaseTrial = {
  trial: number;
  passed: boolean;
  reason: string | null;
  run_id: string | null;
};

export type EvalRunCaseSummary = {
  case_id: string;
  name: string | null;
  axis: string | null;
  pinned: boolean;
  hidden: boolean;
  passed: boolean;
  flaky: boolean;
  trials: EvalRunCaseTrial[];
};

export type EvalRunSummary = {
  id: string;
  agent_id: string;
  version_id: string;
  status: string;
  trials_per_case: number;
  started_at: string;
  finished_at: string | null;
  cases: EvalRunCaseSummary[];
};

export type VerdictCaseSummary = {
  id: string;
  name: string;
  axis: string;
  pinned: boolean;
};

export type Verdict = {
  base_score: number;
  cand_score: number;
  n_visible: number;
  fixed: VerdictCaseSummary[];
  regressed: VerdictCaseSummary[];
  unchanged: VerdictCaseSummary[];
  avg_delta: number;
  by_axis: Record<string, number>;
  axis_breach: Record<string, number>;
  pinned_regressed: VerdictCaseSummary[];
  meets_policy: boolean;
  reasons: string[];
  generalization: { base: number; cand: number; total: number };
};

export type ProposalOp = {
  op: string;
  rule_id?: string;
  text?: string;
  addresses?: string[];
  why?: string;
};

export type ProposalLintEntry = {
  op_index?: number;
  passed?: boolean;
  violations?: unknown[];
  rule_id?: string;
  reason?: string;
  [key: string]: unknown;
};

export type ProposalDiff = {
  before: Guideline[];
  after: Guideline[];
  before_text: string;
  after_text: string;
};

export type Proposal = {
  id: string;
  agent_id: string;
  base_version_id: string;
  candidate_version_id: string | null;
  status: "running" | "ready" | "failed" | "accepted" | "rejected" | string;
  diagnoses: unknown;
  ops: ProposalOp[] | null;
  skipped: unknown;
  lint: ProposalLintEntry[] | null;
  diff: ProposalDiff;
  verdict: Verdict | null;
  base_eval_run: EvalRunSummary | null;
  cand_eval_run: EvalRunSummary | null;
  decision_note: string | null;
  created_at: string;
};

// ---------------------------------------------------------------------------
// API functions
// ---------------------------------------------------------------------------
export const api = {
  health: () => get<Health>("/health"),
  templates: () => get<Template[]>("/templates"),

  createAgent: (body: { name: string; description?: string; template: string }) =>
    post<{ id: string; slug: string; name: string; description: string; deployed_version_id: string | null }>(
      "/agents",
      body
    ),
  listAgents: () => get<AgentListItem[]>("/agents"),
  getAgent: (id: string) => get<AgentDetail>(`/agents/${id}`),
  createVersion: (
    agentId: string,
    body: Partial<{
      system_prompt: string;
      guidelines: Guideline[];
      tools: string[];
      model: string;
      max_steps: number;
      tool_timeout_s: number;
      change_note: string;
    }>
  ) => post<AgentVersion>(`/agents/${agentId}/versions`, body),
  deploy: (agentId: string, versionId: string) =>
    post<{ id: string; deployed_version_id: string }>(`/agents/${agentId}/deploy`, {
      version_id: versionId,
    }),

  shareInfo: (slug: string) => get<ShareInfo>(`/share/${slug}`),
  startShareConversation: (slug: string) =>
    post<{ conversation_id: string }>(`/share/${slug}/conversations`, undefined),
  postMessage: (conversationId: string, content: string) =>
    post<{ run_id: string }>(`/conversations/${conversationId}/messages`, { content }),
  getConversation: (conversationId: string) =>
    get<ConversationDetail>(`/conversations/${conversationId}`),

  runEventsUrl: (runId: string) => `${API_BASE_URL}/runs/${runId}/events`,

  postFeedback: (runId: string, rating: "up" | "down", correction?: string) =>
    post<Feedback>(`/runs/${runId}/feedback`, { rating, correction }),
  listFeedback: (agentId: string, status?: string) =>
    get<Feedback[]>(`/agents/${agentId}/feedback${status ? `?status=${status}` : ""}`),
  draftCase: (feedbackId: string) => post<DraftCase>(`/feedback/${feedbackId}/draft-case`, undefined),
  dismissFeedback: (feedbackId: string) =>
    post<{ id: string; status: string }>(`/feedback/${feedbackId}/dismiss`, undefined),

  createCase: (
    agentId: string,
    body: {
      name: string;
      check_type: string;
      check_spec: Record<string, unknown>;
      axis?: string;
      history?: unknown;
      pinned?: boolean;
      from_feedback_id?: string;
    }
  ) => post<EvalCase>(`/agents/${agentId}/cases`, body),
  patchCase: (
    caseId: string,
    body: Partial<{ pinned: boolean; axis: string; name: string; status: string }>
  ) => patch<EvalCase>(`/cases/${caseId}`, body),
  listCases: (agentId: string) => get<EvalCase[]>(`/agents/${agentId}/cases`),

  createEvalRun: (agentId: string, versionId: string) =>
    post<EvalRunSummary>(`/agents/${agentId}/eval-runs`, { version_id: versionId }),
  getEvalRun: (evalRunId: string) => get<EvalRunSummary>(`/eval-runs/${evalRunId}`),

  getPolicy: (agentId: string) => get<Policy>(`/agents/${agentId}/policy`),
  putPolicy: (agentId: string, body: Partial<Policy>) => put<Policy>(`/agents/${agentId}/policy`, body),

  createProposal: (agentId: string) =>
    post<{ proposal_id: string }>(`/agents/${agentId}/proposals`, undefined),
  getProposal: (proposalId: string) => get<Proposal>(`/proposals/${proposalId}`),
  acceptProposal: (proposalId: string, deploy: boolean, note?: string) =>
    post<Proposal>(`/proposals/${proposalId}/accept`, { deploy, note }),
  rejectProposal: (proposalId: string) => post<Proposal>(`/proposals/${proposalId}/reject`, undefined),
};
