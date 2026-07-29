// Human labels for `onboarding_documents.kind`. Lives here rather than beside
// the HR run page because the public /sign page names the same documents to a
// candidate who has never seen the dashboard, and the two must agree.
export const DOCUMENT_KIND_LABEL: Record<string, string> = {
  loi: "Letter of Intent",
  appointment_letter: "Appointment Letter",
  nda: "NDA",
  induction: "Induction document",
  offer_bundle: "Offer bundle",
};

export function documentKindLabel(kind: string): string {
  return (
    DOCUMENT_KIND_LABEL[kind] ??
    kind.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase())
  );
}

/** "Appointment Letter & NDA" — an envelope is one PDF of one or more kinds. */
export function formatDocumentKinds(kinds: string[] | undefined): string {
  if (!kinds?.length) return "Document";
  return kinds.map(documentKindLabel).join(" & ");
}
