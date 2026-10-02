"use client";

import { use, useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError, RunEvent, ShareInfo } from "@/app/lib/api";
import { ErrorBanner, Spinner } from "@/app/components/Loading";

type TraceEvent = RunEvent;

type Turn = {
  runId: string;
  userContent: string;
  trace: TraceEvent[];
  finalAnswer: string | null;
  status: "running" | "succeeded" | "failed" | "step_limit";
  error: string | null;
  feedback: { rating: "up" | "down"; correction?: string } | null;
  showCorrectionBoxFor: "up" | "down" | null;
};

export default function SharePage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = use(params);

  const [info, setInfo] = useState<ShareInfo | null>(null);
  const [infoError, setInfoError] = useState<string | null>(null);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [startError, setStartError] = useState<string | null>(null);
  const [starting, setStarting] = useState(true);

  const [turns, setTurns] = useState<Turn[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [sendError, setSendError] = useState<string | null>(null);

  const eventSourceRef = useRef<EventSource | null>(null);
  const bottomRef = useRef<HTMLDivElement | null>(null);

  // Load share info (name/description only -- UI-R2: no owner nav/config).
  useEffect(() => {
    let cancelled = false;
    api
      .shareInfo(slug)
      .then((data) => {
        if (!cancelled) setInfo(data);
      })
      .catch((e: unknown) => {
        if (!cancelled) setInfoError(e instanceof ApiError ? e.message : "Failed to load agent");
      });
    return () => {
      cancelled = true;
    };
  }, [slug]);

  // Start a conversation once.
  useEffect(() => {
    let cancelled = false;
    api
      .startShareConversation(slug)
      .then((data) => {
        if (!cancelled) setConversationId(data.conversation_id);
      })
      .catch((e: unknown) => {
        if (!cancelled) setStartError(e instanceof ApiError ? e.message : "Failed to start conversation");
      })
      .finally(() => {
        if (!cancelled) setStarting(false);
      });
    return () => {
      cancelled = true;
    };
  }, [slug]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [turns]);

  const streamRun = useCallback((runId: string) => {
    const es = new EventSource(api.runEventsUrl(runId));
    eventSourceRef.current = es;

    es.onmessage = (ev) => {
      try {
        const event: TraceEvent = JSON.parse(ev.data);
        setTurns((prev) => {
          const next = [...prev];
          const idx = next.findIndex((t) => t.runId === runId);
          if (idx === -1) return prev;
          const turn = { ...next[idx] };
          turn.trace = [...turn.trace, event];
          if (event.type === "message.final") {
            turn.finalAnswer = String(event.content ?? "");
          }
          if (event.type === "run.done") {
            turn.status = (event.status as Turn["status"]) ?? "succeeded";
          }
          if (event.type === "run.error") {
            turn.error = String(event.message ?? "Run failed");
          }
          next[idx] = turn;
          return next;
        });
        if (event.type === "run.done") {
          es.close();
          if (eventSourceRef.current === es) eventSourceRef.current = null;
        }
      } catch {
        // ignore malformed event
      }
    };

    es.onerror = () => {
      // EventSource auto-retries; if the run is actually done the backend
      // stream ends and this fires once, so just close quietly.
      setTurns((prev) => {
        const next = [...prev];
        const idx = next.findIndex((t) => t.runId === runId);
        if (idx === -1) return prev;
        if (next[idx].status === "running") {
          // Leave status as-is; a transient network blip shouldn't be
          // reported as a hard failure. The user can still send more
          // messages once the run clears server-side.
        }
        return next;
      });
    };
  }, []);

  async function handleSend() {
    if (!conversationId || !input.trim() || sending) return;
    const content = input.trim();
    setInput("");
    setSendError(null);
    setSending(true);
    try {
      const { run_id } = await api.postMessage(conversationId, content);
      setTurns((prev) => [
        ...prev,
        {
          runId: run_id,
          userContent: content,
          trace: [],
          finalAnswer: null,
          status: "running",
          error: null,
          feedback: null,
          showCorrectionBoxFor: null,
        },
      ]);
      streamRun(run_id);
    } catch (e: unknown) {
      setSendError(e instanceof ApiError ? e.message : "Failed to send message");
      setInput(content);
    } finally {
      setSending(false);
    }
  }

  function openCorrectionBox(runId: string, rating: "up" | "down") {
    setTurns((prev) =>
      prev.map((t) => (t.runId === runId ? { ...t, showCorrectionBoxFor: rating } : t))
    );
    if (rating === "up") {
      void submitFeedback(runId, "up", undefined);
    }
  }

  async function submitFeedback(runId: string, rating: "up" | "down", correction?: string) {
    try {
      await api.postFeedback(runId, rating, correction);
      setTurns((prev) =>
        prev.map((t) =>
          t.runId === runId
            ? { ...t, feedback: { rating, correction }, showCorrectionBoxFor: null }
            : t
        )
      );
    } catch (e: unknown) {
      setSendError(e instanceof ApiError ? e.message : "Failed to send feedback");
    }
  }

  useEffect(() => {
    return () => {
      eventSourceRef.current?.close();
    };
  }, []);

  if (infoError) {
    return (
      <div className="mx-auto max-w-2xl p-4">
        <ErrorBanner message={infoError} />
      </div>
    );
  }

  return (
    <div className="mx-auto flex min-h-screen w-full max-w-2xl flex-col px-3 py-4 sm:px-4">
      <header className="mb-3 border-b pb-3">
        <h1 className="text-lg font-semibold">{info?.name ?? <Spinner label="Loading agent..." />}</h1>
        {info?.description ? <p className="mt-1 text-sm text-gray-500">{info.description}</p> : null}
      </header>

      {startError ? (
        <div className="mb-3">
          <ErrorBanner message={startError} />
        </div>
      ) : null}
      {sendError ? (
        <div className="mb-3">
          <ErrorBanner message={sendError} />
        </div>
      ) : null}

      <div className="flex-1 space-y-4 overflow-y-auto pb-28">
        {turns.map((turn) => (
          <TurnView
            key={turn.runId}
            turn={turn}
            onThumb={(rating) => openCorrectionBox(turn.runId, rating)}
            onSubmitCorrection={(correction) => submitFeedback(turn.runId, "down", correction)}
            onCancelCorrection={() =>
              setTurns((prev) =>
                prev.map((t) => (t.runId === turn.runId ? { ...t, showCorrectionBoxFor: null } : t))
              )
            }
          />
        ))}
        <div ref={bottomRef} />
      </div>

      <div className="sticky bottom-0 mt-2 flex gap-2 border-t bg-white py-3">
        <input
          className="flex-1 rounded border px-3 py-2 text-sm"
          placeholder={starting ? "Starting conversation..." : "Ask a question..."}
          value={input}
          disabled={starting || !conversationId}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              void handleSend();
            }
          }}
        />
        <button
          className="rounded bg-black px-4 py-2 text-sm font-medium text-white disabled:opacity-40"
          disabled={starting || !conversationId || !input.trim() || sending}
          onClick={() => void handleSend()}
        >
          {sending ? <Spinner label="Sending..." /> : "Send"}
        </button>
      </div>
    </div>
  );
}

function TurnView({
  turn,
  onThumb,
  onSubmitCorrection,
  onCancelCorrection,
}: {
  turn: Turn;
  onThumb: (rating: "up" | "down") => void;
  onSubmitCorrection: (correction: string) => void;
  onCancelCorrection: () => void;
}) {
  const [correction, setCorrection] = useState("");
  const toolSteps = turn.trace.filter((e) => e.type === "tool.call" || e.type === "tool.result");
  const stepCount = turn.trace.filter((e) => e.type === "tool.call").length;

  return (
    <div className="space-y-2">
      <div className="ml-auto max-w-[85%] rounded-lg bg-gray-900 px-3 py-2 text-sm text-white">
        {turn.userContent}
      </div>

      <div className="max-w-[90%] space-y-2">
        {toolSteps.length > 0 ? <TraceDisclosure steps={toolSteps} count={stepCount} /> : null}

        {turn.status === "running" && !turn.finalAnswer ? (
          <div className="rounded-lg bg-gray-100 px-3 py-2 text-sm text-gray-500">
            <Spinner label="Working..." />
          </div>
        ) : null}

        {turn.error ? (
          <div className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-800">
            Error: {turn.error}
          </div>
        ) : null}

        {turn.finalAnswer ? (
          <div className="rounded-lg bg-gray-100 px-3 py-2 text-sm whitespace-pre-wrap">
            {turn.finalAnswer}
          </div>
        ) : null}

        {turn.finalAnswer && !turn.feedback ? (
          <div className="flex items-center gap-2 pl-1">
            <button
              aria-label="thumbs up"
              className="rounded border px-2 py-1 text-sm hover:bg-gray-50"
              onClick={() => onThumb("up")}
            >
              &#128077;
            </button>
            <button
              aria-label="thumbs down"
              className="rounded border px-2 py-1 text-sm hover:bg-gray-50"
              onClick={() => onThumb("down")}
            >
              &#128078;
            </button>
          </div>
        ) : null}

        {turn.feedback ? (
          <div className="pl-1 text-xs text-gray-500">
            Feedback sent: {turn.feedback.rating === "up" ? "\u{1F44D}" : "\u{1F44E}"}
            {turn.feedback.correction ? ` -- "${turn.feedback.correction}"` : ""}
          </div>
        ) : null}

        {turn.showCorrectionBoxFor === "down" ? (
          <div className="space-y-2 rounded border bg-white p-2">
            <textarea
              className="w-full rounded border px-2 py-1 text-sm"
              placeholder="What should the agent have done? (optional)"
              rows={2}
              value={correction}
              onChange={(e) => setCorrection(e.target.value)}
            />
            <div className="flex gap-2">
              <button
                className="rounded bg-black px-3 py-1 text-sm text-white"
                onClick={() => {
                  onSubmitCorrection(correction);
                  setCorrection("");
                }}
              >
                Send feedback
              </button>
              <button className="rounded border px-3 py-1 text-sm" onClick={onCancelCorrection}>
                Cancel
              </button>
            </div>
          </div>
        ) : null}
      </div>
    </div>
  );
}

function TraceDisclosure({ steps, count }: { steps: TraceEvent[]; count: number }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="rounded border bg-white text-xs">
      <button
        className="w-full px-3 py-2 text-left font-medium text-gray-600 hover:bg-gray-50"
        onClick={() => setOpen((o) => !o)}
      >
        {open ? "▼" : "▶"} Worked for {count} step{count === 1 ? "" : "s"}
      </button>
      {open ? (
        <div className="space-y-1 border-t px-3 py-2">
          {steps.map((s, i) =>
            s.type === "tool.call" ? (
              <div key={i} className="text-gray-700">
                <span className="font-mono text-blue-700">call</span> {String(s.name)}(
                {JSON.stringify(s.args)})
              </div>
            ) : (
              <div key={i} className="whitespace-pre-wrap text-gray-500">
                <span className="font-mono text-green-700">result</span>{" "}
                {String(s.output ?? "").slice(0, 500)}
                {s.truncated ? " (truncated)" : ""}
              </div>
            )
          )}
        </div>
      ) : null}
    </div>
  );
}
