"use client";

import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import { CheckCircle2, Clock, Loader2, ShieldCheck } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";

interface Prefill {
  reference_name: string;
  candidate_name: string;
  candidate_role: string;
  company_name: string;
  expires_at: string;
  already_submitted: boolean;
}

interface ErrorEnvelope {
  code?: string;
  message?: string;
  detail?: string;
}

type FormState = {
  worked_together_months: string;
  would_recommend: "yes" | "no" | "";
  strengths: string;
  concerns: string;
  role_description: string;
};

const INITIAL: FormState = {
  worked_together_months: "",
  would_recommend: "",
  strengths: "",
  concerns: "",
  role_description: "",
};

export default function BgvFormPage() {
  const params = useParams<{ token: string }>();
  const token = params?.token ?? "";

  const [prefill, setPrefill] = useState<Prefill | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [submitted, setSubmitted] = useState(false);
  const [form, setForm] = useState<FormState>(INITIAL);
  const [errors, setErrors] = useState<Record<string, string>>({});

  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        const res = await fetch(`/api/onboarding/public/bgv/${token}`);
        const body = (await res.json().catch(() => ({}))) as Prefill & ErrorEnvelope;
        if (cancelled) return;
        if (!res.ok) {
          setLoadError(
            body.detail || body.message || "This link can't be opened.",
          );
          return;
        }
        setPrefill(body);
        if (body.already_submitted) setSubmitted(true);
      } catch {
        if (!cancelled) setLoadError("Network error — try again in a minute.");
      }
    }
    if (token) load();
    return () => {
      cancelled = true;
    };
  }, [token]);

  function validate(): boolean {
    const errs: Record<string, string> = {};
    const months = Number(form.worked_together_months);
    if (!form.worked_together_months || Number.isNaN(months) || months < 0) {
      errs.worked_together_months = "Required";
    }
    if (!form.would_recommend) {
      errs.would_recommend = "Pick one";
    }
    setErrors(errs);
    return Object.keys(errs).length === 0;
  }

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!validate() || submitting) return;
    setSubmitting(true);
    try {
      const res = await fetch(`/api/onboarding/public/bgv/${token}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          worked_together_months: Number(form.worked_together_months),
          would_recommend: form.would_recommend === "yes",
          strengths: form.strengths.trim(),
          concerns: form.concerns.trim(),
          role_description: form.role_description.trim(),
        }),
      });
      if (!res.ok) {
        const body = (await res.json().catch(() => ({}))) as ErrorEnvelope;
        setLoadError(
          body.detail || body.message || "Couldn't submit your response.",
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
          We couldn&apos;t open this reference check
        </h1>
        <p className="mt-2 text-sm text-muted-foreground">{loadError}</p>
        <p className="mt-6 text-xs text-muted-foreground">
          If you believe this is an error, reach out to the HR contact at the
          company that asked you to respond.
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
          Thanks, {prefill.reference_name.split(" ")[0]}.
        </h1>
        <p className="mt-2 text-sm text-muted-foreground">
          Your response has been recorded and shared with{" "}
          {prefill.company_name}&apos;s HR team. You can close this tab.
        </p>
      </section>
    );
  }

  return (
    <section className="mx-auto max-w-2xl px-4 py-12">
      <div className="mb-8 flex items-center gap-3 rounded-lg border border-border bg-muted/30 p-4">
        <ShieldCheck className="h-5 w-5 shrink-0 text-foreground" />
        <p className="text-xs text-muted-foreground">
          You&apos;re responding as a professional reference for{" "}
          <strong className="text-foreground">{prefill.candidate_name}</strong>{" "}
          ({prefill.candidate_role}). Your responses go only to{" "}
          {prefill.company_name}&apos;s HR team.
        </p>
      </div>

      <h1 className="text-2xl font-semibold tracking-tight text-foreground">
        Reference check for {prefill.candidate_name}
      </h1>
      <p className="mt-1 text-sm text-muted-foreground">
        About 3 minutes. No account needed.
      </p>

      <form className="mt-8 space-y-6" onSubmit={onSubmit}>
        <div className="space-y-2">
          <Label htmlFor="worked-months">
            How long did you work with {prefill.candidate_name.split(" ")[0]}?
            (in months)
          </Label>
          <Input
            id="worked-months"
            type="number"
            min={0}
            max={600}
            value={form.worked_together_months}
            onChange={(e) =>
              setForm((f) => ({
                ...f,
                worked_together_months: e.target.value,
              }))
            }
            className="max-w-xs"
            placeholder="e.g. 18"
          />
          {errors.worked_together_months ? (
            <p className="text-xs text-red-600">
              {errors.worked_together_months}
            </p>
          ) : null}
        </div>

        <div className="space-y-2">
          <Label htmlFor="role-desc">
            What was their role and what did they do?
          </Label>
          <Textarea
            id="role-desc"
            rows={3}
            value={form.role_description}
            onChange={(e) =>
              setForm((f) => ({ ...f, role_description: e.target.value }))
            }
            placeholder="A sentence or two is enough."
          />
        </div>

        <div className="space-y-2">
          <Label>Would you recommend hiring them?</Label>
          <div className="flex gap-2">
            {(["yes", "no"] as const).map((opt) => {
              const selected = form.would_recommend === opt;
              return (
                <button
                  key={opt}
                  type="button"
                  onClick={() =>
                    setForm((f) => ({ ...f, would_recommend: opt }))
                  }
                  className={
                    "rounded-md border px-4 py-2 text-sm font-medium capitalize transition-colors " +
                    (selected
                      ? "border-foreground bg-foreground text-background"
                      : "border-border text-foreground hover:bg-muted")
                  }
                >
                  {opt}
                </button>
              );
            })}
          </div>
          {errors.would_recommend ? (
            <p className="text-xs text-red-600">{errors.would_recommend}</p>
          ) : null}
        </div>

        <div className="space-y-2">
          <Label htmlFor="strengths">
            Strengths — what stood out (optional)?
          </Label>
          <Textarea
            id="strengths"
            rows={4}
            value={form.strengths}
            onChange={(e) =>
              setForm((f) => ({ ...f, strengths: e.target.value }))
            }
            placeholder="What they did well, what stood out, where they shone."
          />
        </div>

        <div className="space-y-2">
          <Label htmlFor="concerns">
            Any concerns we should know about (optional)?
          </Label>
          <Textarea
            id="concerns"
            rows={4}
            value={form.concerns}
            onChange={(e) =>
              setForm((f) => ({ ...f, concerns: e.target.value }))
            }
            placeholder="Areas for growth, things that didn't go well, context for HR."
          />
        </div>

        <div className="flex justify-end pt-2">
          <Button type="submit" disabled={submitting}>
            {submitting ? (
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
            ) : null}
            Submit response
          </Button>
        </div>
      </form>
    </section>
  );
}
