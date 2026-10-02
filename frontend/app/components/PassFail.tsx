// UI-R6: pass/fail must show both symbol AND color, never color alone.
export function PassFailDot({ passed }: { passed: boolean }) {
  return (
    <span
      title={passed ? "pass" : "fail"}
      className={
        "inline-flex h-5 w-5 items-center justify-center rounded-full text-xs font-bold " +
        (passed ? "bg-green-100 text-green-700" : "bg-red-100 text-red-700")
      }
    >
      {passed ? "✓" : "✗"}
    </span>
  );
}

export function PassFailBadge({
  passed,
  trueLabel,
  falseLabel,
}: {
  passed: boolean;
  trueLabel: string;
  falseLabel: string;
}) {
  return (
    <span
      className={
        "inline-flex items-center gap-1 rounded px-2 py-1 text-sm font-medium " +
        (passed ? "bg-green-100 text-green-800" : "bg-red-100 text-red-800")
      }
    >
      <span>{passed ? "✓" : "✗"}</span>
      <span>{passed ? trueLabel : falseLabel}</span>
    </span>
  );
}
