"use client";

import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import {
  CheckCircle2,
  Clock,
  Loader2,
  Plus,
  ShieldCheck,
  Trash2,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

interface Prefill {
  candidate_name: string;
  company_name: string;
  role_title: string;
  required_count: number;
  expires_at: string;
  already_submitted: boolean;
}

interface ErrorEnvelope {
  code?: string;
  message?: string;
  detail?: string;
}

interface ReferenceForm {
  name: string;
  email: string;
  phone: string;
  relationship: string;
}

const EMPTY_REF: ReferenceForm = {
  name: "",
  email: "",
  phone: "",
  relationship: "",
};

export default function CandidateReferencesPage() {
  const params = useParams<{ token: string }>();
  const token = params?.token ?? "";

  const [prefill, setPrefill] = useState<Prefill | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [submitted, setSubmitted] = useState(false);
  const [refs, setRefs] = useState<ReferenceForm[]>([]);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        const res = await fetch(`/api/onboarding/public/references/${token}`);
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
        if (body.already_submitted) {
          setSubmitted(true);
        } else {
          // Seed with the required number of empty rows so the candidate sees
          // exactly how many slots they have to fill.
          const count = Math.max(1, body.required_count || 2);
          setRefs(Array.from({ length: count }, () => ({ ...EMPTY_REF })));
        }
      } catch {
        if (!cancelled) setLoadError("Network error — try again in a minute.");
      }
    }
    if (token) load();
    return () => {
      cancelled = true;
    };
  }, [token]);

  function update(idx: number, patch: Partial<ReferenceForm>) {
    setRefs((rs) => rs.map((r, i) => (i === idx ? { ...r, ...patch } : r)));
  }

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!prefill || submitting) return;
    setSubmitError(null);

    const valid = refs
      .filter((r) => r.name.trim() && r.email.trim())
      .map((r) => ({
        name: r.name.trim(),
        email: r.email.trim(),
        phone: r.phone.trim() || undefined,
        relationship: r.relationship.trim() || undefined,
      }));
    if (valid.length < prefill.required_count) {
      setSubmitError(
        `Please fill at least ${prefill.required_count} reference${
          prefill.required_count === 1 ? "" : "s"
        } with a name and email.`,
      );
      return;
    }

    setSubmitting(true);
    try {
      const res = await fetch(
        `/api/onboarding/public/references/${token}`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ references: valid }),
        },
      );
      if (!res.ok) {
        const body = (await res.json().catch(() => ({}))) as ErrorEnvelope;
        setSubmitError(
          body.detail || body.message || "Couldn't submit your references.",
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
          We couldn&apos;t open this form
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
          Thanks, {prefill.candidate_name.split(" ")[0]}.
        </h1>
        <p className="mt-2 text-sm text-muted-foreground">
          Your references have been shared with{" "}
          {prefill.company_name}&apos;s HR team. We&apos;ll reach out to each
          contact over the next few days. You can close this tab.
        </p>
      </section>
    );
  }

  const maxRefs = 4;

  return (
    <section className="mx-auto max-w-2xl px-4 py-12">
      <div className="mb-8 flex items-center gap-3 rounded-lg border border-border bg-muted/30 p-4">
        <ShieldCheck className="h-5 w-5 shrink-0 text-foreground" />
        <p className="text-xs text-muted-foreground">
          You&apos;re submitting background-check reference contacts for your
          role as{" "}
          <strong className="text-foreground">{prefill.role_title}</strong> at{" "}
          <strong className="text-foreground">{prefill.company_name}</strong>.
          Each reference will get a short form sent to their email — they
          don&apos;t need an account.
        </p>
      </div>

      <h1 className="text-2xl font-semibold tracking-tight text-foreground">
        Submit your reference contacts
      </h1>
      <p className="mt-1 text-sm text-muted-foreground">
        Add {prefill.required_count} or more people who can speak about your
        work. About 2 minutes.
      </p>

      <form className="mt-8 space-y-4" onSubmit={onSubmit}>
        {refs.map((r, idx) => (
          <div
            key={idx}
            className="rounded-md border border-border bg-background p-4"
          >
            <div className="mb-3 flex items-center justify-between">
              <p className="text-sm font-semibold">Reference {idx + 1}</p>
              {refs.length > prefill.required_count ? (
                <button
                  type="button"
                  onClick={() => setRefs((rs) => rs.filter((_, i) => i !== idx))}
                  className="inline-flex items-center gap-1 text-[11px] text-muted-foreground hover:text-foreground"
                >
                  <Trash2 className="h-3 w-3" /> Remove
                </button>
              ) : null}
            </div>
            <div className="grid gap-3 sm:grid-cols-2">
              <div className="space-y-1.5">
                <Label htmlFor={`name-${idx}`}>Full name</Label>
                <Input
                  id={`name-${idx}`}
                  value={r.name}
                  onChange={(e) => update(idx, { name: e.target.value })}
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor={`email-${idx}`}>Email</Label>
                <Input
                  id={`email-${idx}`}
                  type="email"
                  value={r.email}
                  onChange={(e) => update(idx, { email: e.target.value })}
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor={`phone-${idx}`}>Phone (optional)</Label>
                <Input
                  id={`phone-${idx}`}
                  value={r.phone}
                  onChange={(e) => update(idx, { phone: e.target.value })}
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor={`rel-${idx}`}>
                  Relationship (e.g. Manager at Acme)
                </Label>
                <Input
                  id={`rel-${idx}`}
                  value={r.relationship}
                  onChange={(e) =>
                    update(idx, { relationship: e.target.value })
                  }
                />
              </div>
            </div>
          </div>
        ))}

        {refs.length < maxRefs ? (
          <button
            type="button"
            onClick={() => setRefs((rs) => [...rs, { ...EMPTY_REF }])}
            className="inline-flex items-center gap-1 text-xs font-medium text-foreground underline hover:no-underline"
          >
            <Plus className="h-3 w-3" /> Add another reference
          </button>
        ) : null}

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
            Submit references
          </Button>
        </div>
      </form>
    </section>
  );
}
