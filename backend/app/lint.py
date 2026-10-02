"""Literal lint (specs/05-improver.md T4.3, IM-8, IM-9, IM-11).

This is the deterministic, safety-critical guard against the improver
"memorizing the case" instead of generalizing a lesson (specs/05 "Threats and
guards" table, row 1: "Memorizing the case | Literal lint + hidden siblings").
A probabilistic prompt (hard rules + one-shot example in
`app/improver_prompt.py`) is the first line of defense; this module is the
backstop that does not depend on the model actually following instructions.

IM-8 defines two independent rejection rules for an op's `text`:
  (a) it contains a "literal" token -- a number with >=2 digits, a 4-digit
      year, a quarter token (Q1-Q4, case-insensitive), or a month name --
      that ALSO appears in the target case's history or correction, OR
  (b) it contains any contiguous 5-word sequence (lowercased, punctuation
      stripped) that also appears in the case's user message or correction.

"The target case's history or correction" / "the case's user message or
correction": when a proposal diagnoses multiple target cases, an op's
`addresses` field says which case(s) it's meant for (per the output schema).
Lint checks an op's text against the UNION of (user message + full history +
correction) of every case id in that op's `addresses` -- if `addresses` is
empty (e.g. a `delete` op, which carries no new text), there is nothing to
lint against and the op always passes rule (a)/(b) on text grounds (it may
still be dropped elsewhere, e.g. for a missing `rule_id`, but that's IM-7's
concern, not lint's).
"""
from __future__ import annotations

import calendar
import re
from dataclasses import dataclass, field
from typing import Optional

_MONTH_NAMES = {m.lower() for m in list(calendar.month_name)[1:]} | {
    m.lower() for m in list(calendar.month_abbr)[1:]
}
_QUARTER_TOKEN_RE = re.compile(r"\bQ[1-4]\b", re.IGNORECASE)
_YEAR_RE = re.compile(r"\b\d{4}\b")
_MULTI_DIGIT_NUMBER_RE = re.compile(r"\b\d[\d,\.]*\d\b|\b\d{2,}\b")
_WORD_RE = re.compile(r"[a-z0-9]+")


@dataclass
class LintViolation:
    kind: str  # "literal_token" | "five_gram"
    value: str  # the offending token/phrase
    source: str  # which field it was found in (always "op_text")


@dataclass
class LintResult:
    op_index: int
    passed: bool
    violations: list[LintViolation] = field(default_factory=list)


def _tokenize_words(text: str) -> list[str]:
    """Lowercased, punctuation-stripped word tokens (IM-8(b))."""
    return _WORD_RE.findall((text or "").lower())


def _five_grams(words: list[str]) -> set[str]:
    return {" ".join(words[i : i + 5]) for i in range(len(words) - 4)} if len(words) >= 5 else set()


def _case_source_text(history: list[dict], correction: Optional[str]) -> str:
    """"the target case's history or correction" (rule a) -- the FULL
    history (every message), not just the user's question, since a literal
    number/date could appear in an earlier turn too.
    """
    parts = [m.get("content") or "" for m in (history or [])]
    if correction:
        parts.append(correction)
    return "\n".join(parts)


def _case_user_message_text(history: list[dict], correction: Optional[str]) -> str:
    """"the case's user message or correction" (rule b) -- every USER message
    in the history (there may be several in a multi-turn case prefix), plus
    the correction.
    """
    parts = [m.get("content") or "" for m in (history or []) if m.get("role") == "user"]
    if correction:
        parts.append(correction)
    return "\n".join(parts)


def _extract_literal_tokens(text: str) -> set[str]:
    """Numbers (>=2 digits), years, quarter tokens, month names found in
    `text`, normalized lowercase for comparison. A 4-digit year also matches
    the >=2-digit-number pattern; that's fine, we only need the SET of
    literal strings present in both the op text and the case text, not which
    specific sub-rule fired for each.
    """
    lowered = text or ""
    tokens: set[str] = set()

    for m in _MULTI_DIGIT_NUMBER_RE.finditer(lowered):
        tokens.add(m.group(0))
    for m in _YEAR_RE.finditer(lowered):
        tokens.add(m.group(0))
    for m in _QUARTER_TOKEN_RE.finditer(lowered):
        tokens.add(m.group(0).upper())
    for word in _WORD_RE.findall(lowered.lower()):
        if word in _MONTH_NAMES:
            tokens.add(word)

    return tokens


def lint_op_text(
    op_text: str,
    *,
    history: list[dict],
    correction: Optional[str],
) -> list[LintViolation]:
    """Run IM-8(a) and IM-8(b) for one op's text against one case's
    history/correction. Returns the list of violations (empty = passes).
    """
    violations: list[LintViolation] = []

    # Rule (a): literal number/year/quarter/month also present in the case.
    op_tokens = _extract_literal_tokens(op_text)
    if op_tokens:
        case_source = _case_source_text(history, correction)
        case_tokens = _extract_literal_tokens(case_source)
        # Quarter tokens are case-normalized to upper (Q3); compare the rest
        # case-insensitively too by lowering both sides for the match itself.
        case_tokens_lower = {t.lower() for t in case_tokens}
        for token in op_tokens:
            if token.lower() in case_tokens_lower:
                violations.append(LintViolation(kind="literal_token", value=token, source="op_text"))

    # Rule (b): any 5-word sequence shared with the case's user message/correction.
    op_words = _tokenize_words(op_text)
    op_grams = _five_grams(op_words)
    if op_grams:
        case_user_text = _case_user_message_text(history, correction)
        case_words = _tokenize_words(case_user_text)
        case_grams = _five_grams(case_words)
        shared = op_grams & case_grams
        for gram in shared:
            violations.append(LintViolation(kind="five_gram", value=gram, source="op_text"))

    return violations


@dataclass
class CaseLintContext:
    case_id: str
    history: list[dict]
    correction: Optional[str] = None


def lint_ops(
    ops: list[dict],
    *,
    cases_by_id: dict[str, CaseLintContext],
    op_index_offset: int = 0,
) -> list[LintResult]:
    """IM-9: run lint on every op, returning one `LintResult` per op in
    order, each `{op_index, passed, violations}`. `op_index` is the index
    into the ORIGINAL (pre-cap) ops list the caller is iterating, offset by
    `op_index_offset` for callers that sliced the list first (mirrors
    `app/ops.py::apply_ops`'s convention).

    An op with an empty/missing `addresses` (e.g. most `delete` ops) has
    nothing case-specific to check its text against and always passes --
    `delete` ops don't introduce new literal text at all.
    """
    results: list[LintResult] = []
    for i, op in enumerate(ops):
        real_index = i + op_index_offset
        addresses = op.get("addresses") or []
        op_text = op.get("text", "") or ""

        if not addresses or not op_text:
            results.append(LintResult(op_index=real_index, passed=True, violations=[]))
            continue

        violations: list[LintViolation] = []
        for case_id in addresses:
            ctx = cases_by_id.get(case_id)
            if ctx is None:
                continue
            violations.extend(lint_op_text(op_text, history=ctx.history, correction=ctx.correction))

        # de-dup identical violations across multiple addressed cases
        seen = set()
        deduped = []
        for v in violations:
            key = (v.kind, v.value)
            if key in seen:
                continue
            seen.add(key)
            deduped.append(v)

        results.append(LintResult(op_index=real_index, passed=not deduped, violations=deduped))
    return results
