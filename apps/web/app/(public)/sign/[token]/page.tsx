"use client";

import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import { EmbedSignDocument } from "@documenso/embed-react";
import { CheckCircle2, Clock, Loader2, ShieldCheck } from "lucide-react";

interface Prefill {
  envelope_id: string;
  role: string;
  signer_name: string;
  document_kinds: string[];
  documenso_host: string;
  documenso_token: string;
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
  const [completed, setCompleted] = useState(false);

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
        if (body.already_signed) setCompleted(true);
      } catch {
        if (!cancelled) setLoadError("Network error — try again in a minute.");
      }
    }
    if (token) load();
    return () => {
      cancelled = true;
    };
  }, [token]);

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

  if (completed) {
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
    <section className="mx-auto max-w-5xl px-4 py-8">
      <div className="mb-5 flex items-center gap-3 rounded-lg border border-border bg-muted/30 p-4">
        <ShieldCheck className="h-5 w-5 shrink-0 text-foreground" />
        <p className="text-xs text-muted-foreground">
          You&apos;re signing as{" "}
          <strong className="text-foreground">{prefill.signer_name}</strong>.
          This is a legally-binding electronic signature — no account needed.
        </p>
      </div>

      <h1 className="text-2xl font-semibold tracking-tight text-foreground">
        Review &amp; sign
      </h1>

      {/* Documenso's signer, embedded on our domain. `host` targets the
          self-hosted instance; `token` is this recipient's signing token from
          the esign adapter's prefill. Completion is finalised server-side via
          the Documenso webhook — this callback only advances the UI.

          The embed forwards className straight to its <iframe> and sets no
          dimensions of its own, so without an explicit size it collapses to
          the 300x150 browser default. Height is the viewport minus this
          page's chrome, floored so short windows still show a usable page. */}
      <div className="mt-5 overflow-hidden rounded-md border border-border bg-background">
        <EmbedSignDocument
          className="block h-[calc(100dvh-16rem)] min-h-[560px] w-full border-0"
          host={prefill.documenso_host}
          token={prefill.documenso_token}
          onDocumentCompleted={() => setCompleted(true)}
        />
      </div>
    </section>
  );
}
