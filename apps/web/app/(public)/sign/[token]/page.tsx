"use client";

import { useParams } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import SignatureCanvas from "react-signature-canvas";
import { CheckCircle2, Clock, Loader2, ShieldCheck } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

interface Prefill {
  envelope_id: string;
  role: string;
  signer_name: string;
  document_kinds: string[];
  preview_url: string | null;
  already_signed: boolean;
}

interface ErrorEnvelope {
  code?: string;
  message?: string;
  detail?: string;
}

export default function SignDocumentPage() {
  const params = useParams<{ token: string }>();
  const token = params?.token ?? "";

  const [prefill, setPrefill] = useState<Prefill | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [submitted, setSubmitted] = useState(false);

  const [mode, setMode] = useState<"draw" | "type">("draw");
  const [typedName, setTypedName] = useState("");
  const [consentAccepted, setConsentAccepted] = useState(false);
  const sigPadRef = useRef<SignatureCanvas | null>(null);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        const res = await fetch(`/api/onboarding/public/sign/${token}`);
        const body = (await res.json().catch(() => ({}))) as Prefill &
          ErrorEnvelope;
        if (cancelled) return;
        if (!res.ok) {
          setLoadError(
            body.detail || body.message || "This link can't be opened.",
          );
          return;
        }
        setPrefill(body);
        if (body.already_signed) setSubmitted(true);
      } catch {
        if (!cancelled) setLoadError("Network error — try again in a minute.");
      }
    }
    if (token) load();
    return () => {
      cancelled = true;
    };
  }, [token]);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!prefill || submitting) return;
    setSubmitError(null);

    if (!consentAccepted) {
      setSubmitError("Please confirm you agree to sign electronically.");
      return;
    }

    let signatureImageBase64: string | null = null;
    let name: string | null = null;
    if (mode === "draw") {
      if (!sigPadRef.current || sigPadRef.current.isEmpty()) {
        setSubmitError("Please draw your signature, or switch to “Type instead”.");
        return;
      }
      signatureImageBase64 = sigPadRef.current
        .getTrimmedCanvas()
        .toDataURL("image/png");
    } else {
      if (!typedName.trim()) {
        setSubmitError("Please type your full name.");
        return;
      }
      name = typedName.trim();
    }

    setSubmitting(true);
    try {
      const res = await fetch(`/api/onboarding/public/sign/${token}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          consent_accepted: true,
          signature_image_base64: signatureImageBase64,
          typed_name: name,
        }),
      });
      if (!res.ok) {
        const body = (await res.json().catch(() => ({}))) as ErrorEnvelope;
        setSubmitError(
          body.detail || body.message || "Couldn't submit your signature.",
        );
        return;
      }
      setSubmitted(true);
    } finally {
      setSubmitting(false);
    }
  }

  if (loadError) {
    return (
      <section className="mx-auto flex min-h-[60vh] max-w-xl flex-col items-center justify-center px-4 text-center">
        <Clock className="mb-4 h-10 w-10 text-zinc-400" />
        <h1 className="text-xl font-semibold tracking-tight text-foreground">
          We couldn&apos;t open this document
        </h1>
        <p className="mt-2 text-sm text-muted-foreground">{loadError}</p>
        <p className="mt-6 text-xs text-muted-foreground">
          If you believe this is an error, reach out to the HR team that sent
          you this link.
        </p>
      </section>
    );
  }

  if (!prefill) {
    return (
      <section className="mx-auto flex min-h-[60vh] max-w-xl flex-col items-center justify-center px-4">
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
      </section>
    );
  }

  if (submitted) {
    return (
      <section className="mx-auto flex min-h-[60vh] max-w-xl flex-col items-center justify-center px-4 text-center">
        <CheckCircle2 className="mb-4 h-10 w-10 text-emerald-500" />
        <h1 className="text-xl font-semibold tracking-tight text-foreground">
          Thanks, {prefill.signer_name.split(" ")[0]}.
        </h1>
        <p className="mt-2 text-sm text-muted-foreground">
          Your signature has been recorded. You can close this tab.
        </p>
      </section>
    );
  }

  return (
    <section className="mx-auto max-w-3xl px-4 py-12">
      <div className="mb-8 flex items-center gap-3 rounded-lg border border-border bg-muted/30 p-4">
        <ShieldCheck className="h-5 w-5 shrink-0 text-foreground" />
        <p className="text-xs text-muted-foreground">
          You&apos;re signing as{" "}
          <strong className="text-foreground">{prefill.signer_name}</strong>.
          This is an electronic signature — no account needed.
        </p>
      </div>

      <h1 className="text-2xl font-semibold tracking-tight text-foreground">
        Review &amp; sign
      </h1>

      {prefill.preview_url ? (
        <div className="mt-6 overflow-hidden rounded-md border border-border">
          <iframe
            src={prefill.preview_url}
            title="Document preview"
            className="h-[60vh] w-full"
          />
        </div>
      ) : (
        <p className="mt-6 text-sm text-muted-foreground">
          Preview unavailable — you can still sign below.
        </p>
      )}

      <form className="mt-8 space-y-4" onSubmit={onSubmit}>
        <div className="rounded-md border border-border bg-background p-4">
          <div className="mb-3 flex gap-2">
            <button
              type="button"
              onClick={() => setMode("draw")}
              className={
                mode === "draw"
                  ? "rounded-full bg-foreground px-3 py-1 text-xs font-medium text-background"
                  : "rounded-full border border-border px-3 py-1 text-xs font-medium text-muted-foreground"
              }
            >
              Draw
            </button>
            <button
              type="button"
              onClick={() => setMode("type")}
              className={
                mode === "type"
                  ? "rounded-full bg-foreground px-3 py-1 text-xs font-medium text-background"
                  : "rounded-full border border-border px-3 py-1 text-xs font-medium text-muted-foreground"
              }
            >
              Type instead
            </button>
          </div>

          {mode === "draw" ? (
            <div className="rounded-md border border-dashed border-border bg-muted/20">
              <SignatureCanvas
                ref={sigPadRef}
                penColor="black"
                canvasProps={{ className: "h-40 w-full" }}
              />
            </div>
          ) : (
            <div className="space-y-1.5">
              <Label htmlFor="typed-name">Full name</Label>
              <Input
                id="typed-name"
                value={typedName}
                onChange={(e) => setTypedName(e.target.value)}
                className="italic"
                style={{ fontFamily: "cursive" }}
              />
            </div>
          )}
          {mode === "draw" ? (
            <button
              type="button"
              onClick={() => sigPadRef.current?.clear()}
              className="mt-2 text-[11px] text-muted-foreground hover:text-foreground"
            >
              Clear
            </button>
          ) : null}
        </div>

        <div className="flex items-start gap-2">
          <Checkbox
            checked={consentAccepted}
            onCheckedChange={setConsentAccepted}
            aria-label="Consent to sign electronically"
            className="mt-0.5"
          />
          <p className="text-xs text-muted-foreground">
            I agree that my typed/drawn signature above constitutes my legal
            signature on this document, and I consent to conducting this
            transaction electronically.
          </p>
        </div>

        {submitError ? (
          <p className="rounded-md border border-red-300/60 bg-red-50 p-3 text-xs text-red-700 dark:border-red-500/30 dark:bg-red-500/10 dark:text-red-200">
            {submitError}
          </p>
        ) : null}

        <div className="flex justify-end pt-2">
          <Button type="submit" disabled={submitting}>
            {submitting ? (
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
            ) : null}
            Sign document
          </Button>
        </div>
      </form>
    </section>
  );
}
