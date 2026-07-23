import type { AnswerQuality, ReviewSignal } from "../types";

/**
 * What the system observed while producing a proposal — not a confidence the
 * model reported about itself.
 *
 * The distinction matters to whoever is approving: "rests on a single reading
 * that does not describe what this action addresses" is checkable, whereas a
 * model asserting high confidence beside a weak proposal just adds false
 * assurance. This never gates anything; approval is required either way.
 */
export function ProposalReview({ review }: { review: ReviewSignal | null }) {
  if (!review) return null;

  const label =
    review.review_priority === "routine" ? "well supported" : "check carefully";

  return (
    <div className="review">
      <div className="head">
        <span className={`badge ${review.review_priority}`}>{label}</span>
        <span className="muted">
          {review.evidence_count} reading{review.evidence_count === 1 ? "" : "s"}
          {review.distinct_fields.length > 0 &&
            ` · ${review.distinct_fields.join(", ")}`}
        </span>
      </div>
      {review.notes.length > 0 && (
        <ul>
          {review.notes.map((note) => (
            <li key={note}>{note}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

/** How cleanly the turn produced its answer. Absent when nothing is notable. */
export function AnswerQualityBlock({ quality }: { quality: AnswerQuality | null }) {
  if (!quality || !quality.degraded) return null;

  return (
    <div className="quality degraded">
      <strong>Worth a closer read</strong>
      <ul>
        {quality.notes.map((note) => (
          <li key={note}>{note}</li>
        ))}
      </ul>
    </div>
  );
}
