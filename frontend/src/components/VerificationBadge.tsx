import type { VerificationState } from "../api/types";

interface VerificationBadgeProps {
  state: VerificationState;
  /** Compact mode: icon + short label only, for dense grids (RatiosTable). */
  compact?: boolean;
}

/**
 * Renders the backend's own verification_state, verbatim — never derives,
 * infers, recomputes, or upgrades it. The backend remains the sole source
 * of truth; this component's only job is honest, readable presentation.
 *
 * Wording is deliberately careful not to overclaim (see
 * TRUST_PROVENANCE_UX_ARCHITECTURE_REVIEW.md Task 3):
 *   - VERIFIED   -> structural self-consistency was confirmed, not
 *                   "guaranteed correct" or "100% accurate".
 *   - NEEDS_REVIEW -> the automated check could not reach certainty;
 *                     manual inspection is appropriate, not "probably wrong".
 *   - null       -> no structural check was possible/available for this
 *                   value (e.g. no table on the page) — presented as a
 *                   neutral "Unverified" state, never fabricated as either
 *                   of the other two.
 * Uses icon + text label together, never color alone, per instruction.
 */
export function VerificationBadge({ state, compact = false }: VerificationBadgeProps) {
  const config = BADGE_CONFIG[state ?? "UNVERIFIED"];
  const title =
    state === "VERIFIED"
      ? "Passed FinTool's structural validation and evidence checks."
      : state === "NEEDS_REVIEW"
        ? "A candidate was found, but the available evidence did not satisfy all checks required for Verified. Manual review is appropriate."
        : "No structural verification was available for this value.";

  return (
    <span className={`verification-badge verification-badge--${config.className}`} title={title}>
      <span className="verification-badge__icon" aria-hidden="true">{config.icon}</span>
      {!compact && <span className="verification-badge__label">{config.label}</span>}
    </span>
  );
}

const BADGE_CONFIG = {
  VERIFIED: { label: "Verified", icon: "✓", className: "verified" },
  NEEDS_REVIEW: { label: "Needs review", icon: "!", className: "needs-review" },
  UNVERIFIED: { label: "Unverified", icon: "–", className: "unverified" },
} as const;
