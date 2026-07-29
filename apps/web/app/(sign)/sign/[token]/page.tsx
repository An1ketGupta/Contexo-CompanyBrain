"use client";

import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import { EmbedSignDocument } from "@documenso/embed-react";
import {
  CheckCircle2,
  FileWarning,
  Loader2,
  RotateCw,
  ShieldCheck,
  XCircle,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { formatDocumentKinds } from "@/lib/onboarding-documents";

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

type LoadError = { title: string; message: string };

type Phase = "signing" | "completed" | "declined";

const ROLE_LABEL: Record<string, string> = {
  hr: "HR",
  candidate: "Candidate",
  signer: "Signer",
};

// If the embed's "document-ready" postMessage never arrives (blocked frame,
// older Documenso build), drop the loading veil anyway rather than trap the
// signer behind a spinner over a perfectly usable document.
const EMBED_READY_TIMEOUT_MS = 12_000;

// The adapter's 404 detail ("Link not recognised.") only restates the heading,
// so that case gets copy that actually tells the recipient something. 410 and
// 409 details are specific — expired vs voided vs declined vs no online signing
// — and are worth showing verbatim.
function describeError(statusCode: number, detail: string): LoadError {
  if (statusCode === 404) {
    return {
      title: "We don't recognise this link",
      message:
        "It may have been mistyped, or the document it pointed to was withdrawn.",
    };
  }
  if (statusCode === 410) {
    return { title: "This link is no longer active", message: detail };
  }
  if (statusCode === 409) {
    return { title: "This document can't be signed online", message: detail };
  }
  return { title: "We couldn't open this document", message: detail };
}

/** Full-height shell for the terminal states — one centred card. */
function Notice({
  icon,
  title,
  children,
}: {
  icon: React.ReactNode;
  title: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex min-h-0 flex-1 items-center justify-center overflow-y-auto px-4 py-10">
      <div className="w-full max-w-md rounded-xl border border-border bg-background p-8 text-center shadow-sm">
        <div className="mx-auto mb-5 flex h-12 w-12 items-center justify-center rounded-full bg-muted">
          {icon}
        </div>
        <h1 className="text-lg font-semibold tracking-tight text-foreground">
          {title}
        </h1>
        <div className="mt-2 space-y-3 text-sm text-muted-foreground">
          {children}
        </div>
      </div>
    </div>
  );
}

export default function SignDocumentPage() {
  const params = useParams<{ token: string }>();
  const token = params?.token ?? "";

  const [prefill, setPrefill] = useState<Prefill | null>(null);
  const [loadError, setLoadError] = useState<LoadError | null>(null);
  const [phase, setPhase] = useState<Phase>("signing");
  const [embedReady, setEmbedReady] = useState(false);
  const [embedError, setEmbedError] = useState(false);

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
            describeError(
              res.status,
              body.detail || body.message || "This link can't be opened.",
            ),
          );
          return;
        }
        setPrefill(body);
        if (body.already_signed) setPhase("completed");
      } catch {
        if (!cancelled) {
          setLoadError({
            title: "We couldn't reach the signing service",
            message: "Check your connection and try again in a minute.",
          });
        }
      }
    }
    if (token) load();
    return () => {
      cancelled = true;
    };
  }, [token]);

  useEffect(() => {
    if (!prefill || embedReady || phase !== "signing") return;
    const t = setTimeout(() => setEmbedReady(true), EMBED_READY_TIMEOUT_MS);
    return () => clearTimeout(t);
  }, [prefill, embedReady, phase]);

  if (loadError) {
    return (
      <Notice
        icon={<FileWarning className="h-6 w-6 text-muted-foreground" />}
        title={loadError.title}
      >
        <p>{loadError.message}</p>
        <p className="text-xs">
          Reach out to the HR team that sent you this link — they can issue a
          fresh one.
        </p>
      </Notice>
    );
  }

  if (!prefill) {
    return (
      <div className="flex min-h-0 flex-1 items-center justify-center">
        <div className="flex items-center gap-2.5 text-sm text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" />
          Opening your document…
        </div>
      </div>
    );
  }

  const documentTitle = formatDocumentKinds(prefill.document_kinds);
  const firstName = prefill.signer_name.split(" ")[0];

  if (phase === "completed") {
    return (
      <Notice
        icon={<CheckCircle2 className="h-6 w-6 text-success-ink" />}
        title={`That's signed, ${firstName}.`}
      >
        <p>
          Your signature on the {documentTitle} has been recorded. A completed
          copy will be emailed to you once every signer is done.
        </p>
        <p className="text-xs">You can close this tab.</p>
      </Notice>
    );
  }

  if (phase === "declined") {
    return (
      <Notice
        icon={<XCircle className="h-6 w-6 text-destructive-ink" />}
        title="You chose not to sign"
      >
        <p>
          Nothing has been signed. If that wasn&apos;t what you meant to do,
          contact the HR team that sent you this link.
        </p>
        <p className="text-xs">You can close this tab.</p>
      </Notice>
    );
  }

  return (
    <div className="mx-auto flex min-h-0 w-full max-w-6xl flex-1 flex-col gap-3 px-4 py-4 sm:px-6">
      <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <h1 className="truncate text-base font-semibold tracking-tight text-foreground">
              {documentTitle}
            </h1>
            <Badge variant="outline" className="shrink-0">
              {ROLE_LABEL[prefill.role] ?? prefill.role}
            </Badge>
          </div>
          <p className="mt-0.5 truncate text-xs text-muted-foreground">
            Signing as{" "}
            <span className="font-medium text-foreground">
              {prefill.signer_name}
            </span>
          </p>
        </div>

        <span className="inline-flex items-center gap-1.5 rounded-full border border-border bg-background px-2.5 py-1 text-[11px] text-muted-foreground">
          <ShieldCheck className="h-3.5 w-3.5 shrink-0 text-brand" />
          Legally binding e-signature — no account needed
        </span>
      </div>

      {/* Documenso's signer, embedded on our domain. `host` targets the
          self-hosted instance; `token` is this recipient's signing token from
          the esign adapter's prefill. Completion is finalised server-side via
          the Documenso webhook — the callbacks below only advance this UI.

          The embed forwards className straight to its <iframe> and sets no
          dimensions of its own, so it collapses to the 300x150 browser default
          without an explicit size. `h-full` against this flex-1 parent hands it
          exactly the viewport left over from the header, which keeps the iframe
          the only scroller on the page.

          `darkModeDisabled` pins the signer to its light theme. Our app follows
          next-themes while the embed follows the OS, so left alone the two
          disagree and the document sits in dark chrome on a light page (or the
          reverse). Pinned light, it reads as paper — which is what the PDF
          inside it is in every theme. Its palette can't be matched to ours:
          `cssVars`/`css` are only injected when Documenso's organisation claim
          has embedSigningWhiteLabel set, so on this instance they are ignored.

          The library registers its postMessage listener once, on first mount,
          closing over that render's props — so these callbacks must never read
          component state. Setters and constants only. */}
      <div className="relative min-h-0 flex-1 overflow-hidden rounded-xl border border-border bg-background shadow-sm">
        {embedError ? (
          <div className="flex h-full flex-col items-center justify-center gap-4 px-6 text-center">
            <FileWarning className="h-6 w-6 text-muted-foreground" />
            <div className="space-y-1">
              <p className="text-sm font-medium text-foreground">
                The document viewer stopped responding
              </p>
              <p className="text-xs text-muted-foreground">
                Nothing you&apos;ve signed so far is lost — reloading picks it
                back up.
              </p>
            </div>
            <Button
              variant="outline"
              size="sm"
              onClick={() => window.location.reload()}
            >
              <RotateCw className="h-3.5 w-3.5" />
              Reload
            </Button>
          </div>
        ) : (
          <>
            <EmbedSignDocument
              className="h-full w-full border-0"
              host={prefill.documenso_host}
              token={prefill.documenso_token}
              name={prefill.signer_name}
              darkModeDisabled
              onDocumentReady={() => setEmbedReady(true)}
              onDocumentCompleted={() => setPhase("completed")}
              onDocumentRejected={() => setPhase("declined")}
              onDocumentError={() => setEmbedError(true)}
            />
            {!embedReady && (
              <div className="absolute inset-0 flex items-center justify-center gap-2.5 bg-white text-sm text-zinc-500">
                <Loader2 className="h-4 w-4 animate-spin" />
                Loading your document…
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
