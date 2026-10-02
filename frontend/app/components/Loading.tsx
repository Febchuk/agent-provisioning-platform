// UI-R8: every async action needs a visible loading state and an error
// state. These small shared pieces keep that consistent across screens.
export function Spinner({ label }: { label?: string }) {
  return (
    <span className="inline-flex items-center gap-2 text-sm text-gray-500">
      <span className="h-3 w-3 animate-spin rounded-full border-2 border-gray-300 border-t-gray-600" />
      {label ?? "Loading..."}
    </span>
  );
}

export function ErrorBanner({ message }: { message: string }) {
  return (
    <div className="rounded border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-800">
      {message}
    </div>
  );
}

export function SandboxBanner({ sandboxMode }: { sandboxMode: string | null }) {
  // UI-R7: owner screens show a banner when the sandbox is not isolated.
  if (sandboxMode !== "local-unsafe") return null;
  return (
    <div className="rounded border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-900">
      Sandbox: local, not isolated
    </div>
  );
}
