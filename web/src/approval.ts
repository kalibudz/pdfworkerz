/**
 * The two questions an edit may need to ask the user (SPEC.md §5.3 and §5.6):
 *
 * - Before: an edit drawn with anything weaker than the exact font needs
 *   approval. The Op is tried with require_tier "exact"; the engine refuses it
 *   (and the journal rolls it back) if the font it resolved is weaker, and only
 *   then is the user asked whether to go ahead anyway.
 * - After: the engine re-renders the page and checks the edit (FNT-12). If that
 *   check fails, the user is told what it found and can undo the edit.
 */

import { ApiError, type Api, type HistoryOp } from "./api";

const WEAKER_TIER = /fell back to tier '(\w+)'/;

/** Applies `op`, asking first if it can't use the exact font. Resolves to the
 * Op's result, or null if the user declined; any other failure is thrown as-is. */
export async function applyWithApproval(
  api: Api,
  documentId: string,
  op: HistoryOp,
): Promise<{ result: unknown } | null> {
  try {
    return { result: await api.applyOp(documentId, { ...op, require_tier: "exact" }) };
  } catch (error) {
    const weaker = error instanceof ApiError && error.status === 400 ? WEAKER_TIER.exec(error.detail) : null;
    if (!weaker) {
      throw error;
    }
    const proceed = window.confirm(
      `This change can't use the original font exactly; it would use a ${weaker[1]} match ` +
        `(${error instanceof ApiError ? error.detail : ""}). Continue?`,
    );
    if (!proceed) {
      return null;
    }
  }
  return { result: await api.applyOp(documentId, { ...op, require_tier: "fallback" }) };
}

interface Verification {
  text_matches: boolean;
  looks_right: boolean;
  outside_changed_fraction: number;
  diff: { changed_fraction: number };
}

function verifications(result: unknown): Verification[] {
  const items = Array.isArray(result) ? result : [result];
  return items
    .map((item) => (item && typeof item === "object" ? (item as { verification?: unknown }).verification : null))
    .filter((v): v is Verification => !!v && typeof v === "object" && "looks_right" in v);
}

/** What the post-edit check found wrong, in plain words, or null if it passed (or didn't run). */
export function verificationProblem(result: unknown): string | null {
  const failed = verifications(result).find((v) => !v.looks_right);
  if (!failed) {
    return null;
  }
  const problems = [];
  if (!failed.text_matches) {
    problems.push("the new text could not be read back from the page");
  }
  if (failed.outside_changed_fraction > 0) {
    problems.push("something outside the edited text changed too");
  }
  if (failed.diff.changed_fraction === 0) {
    problems.push("nothing on the page visibly changed");
  }
  return problems.join(", and ") || "the result did not look right";
}

/** After a successful edit: if its check failed, say so and offer to undo it. */
export async function confirmVerified(api: Api, documentId: string, result: unknown): Promise<void> {
  const problem = verificationProblem(result);
  if (problem && !window.confirm(`The change was made, but checking the page afterwards found that ${problem}. Keep it? (Cancel undoes it.)`)) {
    await api.undo(documentId);
  }
}
