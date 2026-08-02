"use client";

import { useRef, useState } from "react";
import {
  CheckCircle2,
  ExternalLink,
  Loader2,
  Pencil,
  Upload,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { DraftTextEditor } from "@/components/onboarding/draft-text-editor";
import type { RunStep } from "@/components/onboarding/step-panel";
import { cn } from "@/lib/utils";

/** The bits of a run document this panel reads. Structural so the run page can
 *  pass its own row type without a cast. */
export interface ReviewDocument {
  kind: string;
  signed_url: string | null;
  sign_status: string;
  hr_edit_revision: number;
  esign_envelope_id: string | null;
  esign_signers: { role: string; name: string; status: string }[];
}

/** How a signer role reads in the routing summary. */
const SIGNER_LABEL: Record<string, string> = {
  hr: "HR",
  candidate: "candidate",
};

function relativeTime(iso: string | null): string | null {
  if (!iso) return null;
  const min = Math.floor((Date.now() - new Date(iso).getTime()) / 60_000);
  if (min < 1) return "just now";
  if (min < 60) return `${min}m ago`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr}h ago`;
  const d = Math.floor(hr / 24);
  if (d < 30) return `${d}d ago`;
  return new Date(iso).toLocaleDateString();
}

async function errorFrom(res: Response, fallback: string): Promise<string> {
  const body = (await res.json().catch(() => null)) as {
    detail?: string;
    message?: string;
  } | null;
  return body?.detail || body?.message || fallback;
}

/**
 * Everything HR does with a document the run generated: read it, correct it,
 * send it for signature, then watch it get signed.
 *
 * One panel for every `generate` step rather than one per document kind. This
 * used to be two — an LOI panel wired to `/loi/*` endpoints, and a bundle panel
 * that could only preview and approve — so a pipeline with an offer letter, or
 * anything else an org added to its catalog, reached the review gate with no
 * way to open the draft at all.
 */
export function DocumentReviewPanel({
  runId,
  steps,
  documents,
  candidateName,
  onChanged,
}: {
  runId: string;
  /** The step's bundle: usually one document, several when the org grouped
   *  them. A bundle is reviewed, signed and sent as one thing, so the actions
   *  below act on the lead step and the cards list every member. */
  steps: RunStep[];
  documents: ReviewDocument[];
  candidateName: string;
  onChanged: () => Promise<unknown>;
}) {
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  /** Which document is open in the text editor, by step key. */
  const [editing, setEditing] = useState<string | null>(null);
  const signedInput = useRef<HTMLInputElement>(null);
  const docxInput = useRef<HTMLInputElement>(null);

  const lead = steps[0];
  const label = lead.bundle_label ?? lead.label;
  const documentLabel = steps.length === 1 ? label : "documents";
  const stepPath = `/api/onboarding/runs/${runId}/steps/${encodeURIComponent(lead.step_key)}`;

  const leadDoc = documents.find((d) => d.kind === lead.step_key);
  const inReview = lead.status === "pending_hr_review";

  // One step status covers both ways of getting signed. An envelope on the
  // document is what tells them apart: with one, signing happens in the
  // browser; without one, HR prints, signs and uploads the scan.
  const hasEnvelope = Boolean(leadDoc?.esign_envelope_id);
  const awaitingManualSign = lead.status === "pending_signature" && !hasEnvelope;
  const inEsign = lead.status === "pending_signature" && hasEnvelope;

  // Per-signer progress in the routing order the step defines. `esign_signers`
  // is authoritative once an envelope exists; before that the step's roles are
  // all there is to show.
  const signerRoles = lead.signer_roles.length
    ? lead.signer_roles
    : (leadDoc?.esign_signers ?? []).map((s) => s.role);
  const signers = signerRoles.map((role) => {
    const fromEnvelope = leadDoc?.esign_signers?.find((s) => s.role === role);
    return {
      role,
      name:
        role === "candidate"
          ? candidateName || "Candidate"
          : fromEnvelope?.name || "You (HR)",
      signed: fromEnvelope?.status === "completed",
    };
  });
  const hrSigned = signers.find((s) => s.role === "hr")?.signed ?? false;
  const firstUnsigned = signers.find((s) => !s.signed);
  const myTurn = firstUnsigned?.role === "hr";

  async function act(
    key: string,
    request: () => Promise<Response>,
    fallback: string,
  ): Promise<Record<string, unknown> | null> {
    setBusy(key);
    setError(null);
    try {
      const res = await request();
      if (!res.ok) {
        // 409 on the signing link means the backend reconciled and found HR
        // already signed — refresh so the page picks up the new statuses.
        if (res.status === 409) await onChanged();
        setError(await errorFrom(res, fallback));
        return null;
      }
      return (await res.json().catch(() => ({}))) as Record<string, unknown>;
    } catch {
      setError("Couldn't reach the server. Check your connection and retry.");
      return null;
    } finally {
      setBusy(null);
    }
  }

  async function sendForSignature() {
    const prompt = signerRoles.length
      ? `Send ${label} for signature? You won't be able to edit it once sent.`
      : `Send ${label} to the candidate? Nobody is set to sign it, so it goes out as-is.`;
    if (!confirm(prompt)) return;
    const body = await act(
      "approve",
      () => fetch(`${stepPath}/approve`, { method: "POST" }),
      `Couldn't send ${label} for signature.`,
    );
    if (body) await onChanged();
  }

  async function downloadDocx(stepKey: string) {
    const body = await act(
      `docx-${stepKey}`,
      () =>
        fetch(
          `/api/onboarding/runs/${runId}/steps/${encodeURIComponent(stepKey)}/docx-url`,
        ),
      "Couldn't get a download link. Try again.",
    );
    const url = body?.docx_url;
    if (typeof url === "string") window.open(url, "_blank");
  }

  async function replaceDraft(file: File) {
    const form = new FormData();
    form.append("file", file);
    const body = await act(
      "replace-draft",
      () => fetch(`${stepPath}/replace-draft`, { method: "POST", body: form }),
      "Couldn't upload the edited .docx.",
    );
    if (body) await onChanged();
  }

  async function uploadSigned(file: File) {
    const form = new FormData();
    form.append("file", file);
    const body = await act(
      "upload-signed",
      () => fetch(`${stepPath}/upload-signed`, { method: "POST", body: form }),
      "Couldn't upload the signed PDF.",
    );
    if (body) await onChanged();
  }

  async function openSigningLink() {
    const body = await act(
      "signing-url",
      () => fetch(`${stepPath}/signing-url`),
      "Couldn't open the signing link.",
    );
    const url = body?.signing_url;
    if (typeof url === "string") window.open(url, "_blank");
  }

  const editingStep = steps.find((s) => s.step_key === editing);
  const sentAt = lead.status === "done" ? relativeTime(lead.completed_at) : null;

  return (
    <div className="space-y-3">
      <div className="grid gap-2 sm:grid-cols-2">
        {steps.map((step) => (
          <DocumentCard
            key={step.id}
            step={step}
            doc={documents.find((d) => d.kind === step.step_key)}
          />
        ))}
      </div>

      {sentAt ? (
        <p className="text-xs text-muted-foreground">
          Sent to the candidate {sentAt}.
        </p>
      ) : null}

      {error ? (
        <p className="rounded-lg bg-destructive-soft px-3 py-2 text-xs font-medium text-destructive">
          {error}
        </p>
      ) : null}

      {inReview ? (
        <div className="space-y-3 rounded-2xl border border-border bg-background/80 p-4 shadow-sm">
          <div className="space-y-1">
            <p className="text-sm font-semibold text-foreground">
              {editingStep
                ? `Editing ${editingStep.label}`
                : `Review ${documentLabel} before sending`}
            </p>
            <p className="text-xs leading-5 text-muted-foreground">
              {editingStep ? (
                <>
                  Change any line below and save — the PDF re-renders straight
                  away. Lines you don&rsquo;t touch keep their original
                  formatting.
                </>
              ) : signerRoles.length ? (
                <>
                  Open each preview, correct anything that&rsquo;s wrong, then
                  send the document package for signature. It goes to{" "}
                  <strong>
                    {signers
                      .map((s) => SIGNER_LABEL[s.role] ?? s.role)
                      .join(" → ")}
                  </strong>
                  , each signer emailed a link when it reaches them.
                </>
              ) : (
                <>
                  Nobody is set to sign this document, so approving sends it
                  straight to the candidate. Change who signs in Settings →
                  Onboarding.
                </>
              )}
            </p>
          </div>

          {editingStep ? (
            <DraftTextEditor
              runId={runId}
              stepKey={editingStep.step_key}
              label={editingStep.label}
              onSaved={onChanged}
              onClose={() => setEditing(null)}
            />
          ) : (
            <div className="grid gap-3 lg:grid-cols-[minmax(0,1.15fr)_minmax(260px,0.85fr)]">
              <div className="rounded-xl border border-border bg-muted/20 p-3">
                <p className="text-[11px] font-semibold uppercase tracking-[0.16em] text-muted-foreground">
                  Edit
                </p>
                <div className="mt-2 flex flex-wrap gap-2">
                  {steps.map((step) => (
                    <Button
                      key={step.id}
                      variant="outline"
                      size="sm"
                      onClick={() => setEditing(step.step_key)}
                      className="h-8 px-3 text-xs"
                    >
                      <Pencil className="h-3.5 w-3.5" />
                      {steps.length === 1 ? "Edit here" : step.label}
                    </Button>
                  ))}
                </div>
              </div>

              <div className="rounded-xl border border-border bg-muted/20 p-3">
                <p className="text-[11px] font-semibold uppercase tracking-[0.16em] text-muted-foreground">
                  Send
                </p>
                <Button
                  size="sm"
                  onClick={sendForSignature}
                  disabled={busy === "approve"}
                  className="mt-2 w-full justify-center"
                >
                  {busy === "approve" ? (
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  ) : (
                    <CheckCircle2 className="h-3.5 w-3.5" />
                  )}
                  {signerRoles.length
                    ? "Send for signature"
                    : "Approve and send to candidate"}
                </Button>
              </div>

              <div className="rounded-xl border border-border bg-muted/20 p-3 lg:col-span-2">
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <p className="text-[11px] font-semibold uppercase tracking-[0.16em] text-muted-foreground">
                      Word round-trip
                    </p>
                    <p className="mt-1 text-xs leading-5 text-muted-foreground">
                      Use this only if you need to change structure or formatting
                      outside the editor.
                    </p>
                  </div>
                  <input
                    ref={docxInput}
                    type="file"
                    accept=".docx,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                    className="hidden"
                    onChange={(e) => {
                      const f = e.target.files?.[0];
                      if (f) replaceDraft(f);
                      if (docxInput.current) docxInput.current.value = "";
                    }}
                  />
                </div>

                <div className="mt-3 flex flex-wrap gap-2">
                  {steps.map((step) => (
                    <Button
                      key={step.id}
                      type="button"
                      variant="outline"
                      size="sm"
                      onClick={() => downloadDocx(step.step_key)}
                      disabled={busy === `docx-${step.step_key}`}
                      className="h-8 px-3 text-xs"
                    >
                      {busy === `docx-${step.step_key}` ? (
                        <Loader2 className="h-3.5 w-3.5 animate-spin" />
                      ) : (
                        <ExternalLink className="h-3.5 w-3.5" />
                      )}
                      {steps.length === 1 ? "Download DOCX" : step.label}
                    </Button>
                  ))}
                  <Button
                    type="button"
                    variant="secondary"
                    size="sm"
                    onClick={() => docxInput.current?.click()}
                    disabled={busy === "replace-draft"}
                    className="h-8 px-3 text-xs"
                  >
                    {busy === "replace-draft" ? (
                      <Loader2 className="h-3.5 w-3.5 animate-spin" />
                    ) : (
                      <Upload className="h-3.5 w-3.5" />
                    )}
                    Upload edited DOCX
                  </Button>
                </div>
              </div>
            </div>
          )}
        </div>
      ) : null}

      {awaitingManualSign ? (
        <div className="rounded-xl border border-dashed border-border bg-muted/40 p-4">
          <p className="text-sm">
            Print {label}, sign it, scan, then upload the signed PDF here.
          </p>
          <div className="mt-3 flex items-center gap-2">
            <input
              ref={signedInput}
              type="file"
              accept="application/pdf"
              className="hidden"
              onChange={(e) => {
                const f = e.target.files?.[0];
                if (f) uploadSigned(f);
                if (signedInput.current) signedInput.current.value = "";
              }}
            />
            <Button
              size="sm"
              onClick={() => signedInput.current?.click()}
              disabled={busy === "upload-signed"}
            >
              {busy === "upload-signed" ? (
                <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
              ) : (
                <Upload className="mr-1.5 h-3.5 w-3.5" />
              )}
              Upload signed PDF
            </Button>
          </div>
        </div>
      ) : null}

      {inEsign ? (
        <div className="space-y-3 rounded-xl border border-brand/30 bg-brand-tint p-4">
          <div>
            <p className="text-sm font-medium">Signing {label}</p>
            <p className="mt-1 text-xs text-muted-foreground">
              {!firstUnsigned ? (
                <>Everyone has signed. Sending it on.</>
              ) : hrSigned || !signerRoles.includes("hr") ? (
                <>
                  Waiting on <strong>{firstUnsigned.name}</strong>. They&apos;ve
                  been emailed a signing link.
                </>
              ) : (
                <>
                  The envelope is routed{" "}
                  <strong>
                    {signers.map((s) => SIGNER_LABEL[s.role] ?? s.role).join(" → ")}
                  </strong>
                  . Each signer is emailed automatically when it reaches them.
                </>
              )}
            </p>
          </div>

          <dl className="grid grid-cols-1 gap-2 text-xs sm:grid-cols-2">
            {signers.map((signer, i) => (
              <div
                key={signer.role}
                className={cn(
                  "flex items-center justify-between rounded-lg border px-3 py-2",
                  signer.signed
                    ? "border-success/30 bg-success-tint"
                    : "border-border bg-muted/40",
                )}
              >
                <dt className="font-medium text-foreground">{signer.name}</dt>
                <dd
                  className={
                    signer.signed
                      ? "font-medium text-success-ink"
                      : "text-muted-foreground"
                  }
                >
                  {signer.signed
                    ? "Signed ✓"
                    : signer.role === firstUnsigned?.role
                      ? "Pending"
                      : `Waiting for ${signers[i - 1]?.name ?? "the previous signer"}`}
                </dd>
              </div>
            ))}
          </dl>

          {myTurn ? (
            <div className="flex flex-wrap items-center gap-2">
              <Button
                size="sm"
                onClick={openSigningLink}
                disabled={busy === "signing-url"}
              >
                {busy === "signing-url" ? (
                  <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
                ) : (
                  <ExternalLink className="mr-1.5 h-3.5 w-3.5" />
                )}
                Open my signing link
              </Button>
              <p className="text-[11px] text-muted-foreground">
                Link expires after 5 minutes — click again for a fresh one.
              </p>
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

/** One document in the group: what it is, where it's got to, and a preview. */
function DocumentCard({
  step,
  doc,
}: {
  step: RunStep;
  doc: ReviewDocument | undefined;
}) {
  return (
    <div className="rounded-2xl border border-border/80 bg-gradient-to-b from-background to-muted/40 p-4 shadow-sm">
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.16em] text-muted-foreground">
            Document
          </p>
          <p className="mt-1 text-sm font-semibold text-foreground">{step.label}</p>
        </div>
        {doc ? (
          <span className="rounded-full border border-border bg-background px-2 py-1 text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
            {doc.sign_status.replace(/_/g, " ")}
          </span>
        ) : null}
      </div>
      {doc ? (
        <>
          <p className="mt-2 text-[11px] text-muted-foreground">
            Revision {doc.hr_edit_revision + 1}
            {doc.hr_edit_revision > 0 ? (
              <span className="ml-1.5 rounded-full bg-amber-tint px-1.5 py-0.5 text-[10px] font-bold text-amber">
                edited
              </span>
            ) : null}
          </p>
          <div className="mt-3 flex flex-wrap items-center gap-2">
            {doc.signed_url ? (
              <Button asChild size="sm" variant="outline" className="gap-1.5">
                <a href={doc.signed_url} target="_blank" rel="noopener noreferrer">
                  <ExternalLink className="h-3.5 w-3.5" />
                  Open preview
                </a>
              </Button>
            ) : (
              <Button size="sm" variant="outline" disabled>
                Generating preview…
              </Button>
            )}
          </div>
        </>
      ) : (
        <div className="mt-3 rounded-lg border border-dashed border-border bg-background/60 px-3 py-2 text-xs text-muted-foreground">
          This document has not been generated yet.
        </div>
      )}
    </div>
  );
}
