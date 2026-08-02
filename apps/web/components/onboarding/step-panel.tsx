export interface RunStep {
  id: string;
  step_key: string;
  kind: "generate" | "collect" | "system";
  label: string;
  /**
   * Which template a `generate` step renders. The only stable way to find a
   * particular document — `step_key` is whatever the org named the step.
   */
  document_type_key: string | null;
  bundle_key: string | null;
  bundle_label: string | null;
  position: number;
  status: string;
  signer_roles: string[];
  system_action: string | null;
  blocked_reason: string | null;
  started_at: string | null;
  completed_at: string | null;
  /**
   * Whether this step stops for HR once the candidate has acted. False on a
   * step the candidate never touches, and on runs that started before the gate
   * existed — those keep the behaviour they began with.
   */
  requires_hr_approval: boolean;
  /** HR's verdict on the current round, or null if they haven't looked yet. */
  review_decision: "approved" | "rejected" | null;
  review_note: string | null;
  reviewed_at: string | null;
  /** How many times HR has sent this step back. 0 on the first attempt. */
  approval_round: number;
}

/** The panels the run page shows for the step it is sitting in. */
export type BuiltInPanel = "document" | "bgv" | "policies" | "induction";

/**
 * Which panel a step wants, or null if it has none.
 *
 * A step is classified by what it *does* — its document type or its system
 * action — never by `step_key`, which is whatever the org named it.
 *
 * Every `generate` step gets the document panel. It used to be enumerated —
 * one panel for the LOI, one for the appointment-letter-and-NDA pair, nothing
 * for anything else — so a catalog with an offer letter in it stopped at the
 * review gate with no preview to open and no way to send it for signature.
 * What HR does with a generated document is the same whatever it is called.
 */
export function builtInPanelFor(step: RunStep | null): BuiltInPanel | null {
  if (!step) return null;
  if (step.kind === "system") {
    if (step.system_action === "bgv") return "bgv";
    if (step.system_action === "policies") return "policies";
    return null;
  }
  if (step.kind !== "generate") return null;
  // `step_key` as a fallback only: it is the document type for a run whose
  // steps predate `document_type_key` being carried onto the snapshot.
  const type = step.document_type_key ?? step.step_key;
  // The induction pack is generated but never signed — it has no review gate
  // and nothing to approve, just a link once it has gone out.
  if (type === "induction") return "induction";
  return "document";
}
