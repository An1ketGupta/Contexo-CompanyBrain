"use client";

import { useEffect, useState } from "react";
import { Loader2 } from "lucide-react";
import { toast } from "sonner";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { useCurrentUser } from "@/hooks/use-user";
import { useOrganization } from "@/hooks/use-organization";
import { cn } from "@/lib/utils";

type UseCase =
  | "hr_policies"
  | "sales_enablement"
  | "customer_support"
  | "engineering"
  | "general";

const INDUSTRIES = [
  "SaaS / Software",
  "E-commerce",
  "Healthcare",
  "Finance",
  "Agency / Consulting",
  "Education",
  "Real estate",
  "Manufacturing",
  "Nonprofit",
  "Other",
] as const;

const SIZES = ["1-10", "11-50", "51-200", "200-1000", "1000+"] as const;

const USE_CASES: { value: UseCase; label: string; desc: string }[] = [
  {
    value: "hr_policies",
    label: "HR & People Ops",
    desc: "Policy Q&A, handbooks, onboarding docs",
  },
  {
    value: "sales_enablement",
    label: "Sales Enablement",
    desc: "Pitch decks, objection handling, case studies",
  },
  {
    value: "customer_support",
    label: "Customer Support",
    desc: "Playbooks, refund policies, response drafts",
  },
  {
    value: "engineering",
    label: "Engineering",
    desc: "Runbooks, architecture docs, postmortems",
  },
  {
    value: "general",
    label: "General Knowledge Base",
    desc: "Mixed use across multiple teams",
  },
];

const DISMISS_KEY = "v5-enrichment-skip";

/** Shown once to a fresh admin who hasn't filled in industry / size / use
 *  case. We hide the close affordance — the modal can be skipped (sets a
 *  localStorage bit so it doesn't re-prompt this session) but the X corner
 *  button isn't there because admins routinely close dialogs by reflex and
 *  we want a deliberate skip.
 */
export function EnrichmentModal() {
  const { user } = useCurrentUser();
  const { organization, refresh } = useOrganization();

  // Render condition is intentionally conservative: admin + no industry set +
  // no completion timestamp + the user hasn't asked us to leave them alone.
  const isAdmin = user?.role === "admin";
  const needsEnrichment =
    isAdmin && organization && !organization.industry && !organization.onboarding_completed_at;

  const [skipped, setSkipped] = useState(false);
  useEffect(() => {
    if (typeof window !== "undefined") {
      setSkipped(window.localStorage.getItem(DISMISS_KEY) === "1");
    }
  }, []);

  const [open, setOpen] = useState(false);
  useEffect(() => {
    if (needsEnrichment && !skipped) {
      // Defer slightly so the dashboard mount toast / confetti don't fight
      // the modal for the user's attention on first arrival.
      const t = window.setTimeout(() => setOpen(true), 600);
      return () => window.clearTimeout(t);
    }
  }, [needsEnrichment, skipped]);

  const [step, setStep] = useState<"industry" | "size" | "use_case">("industry");
  const [industry, setIndustry] = useState<string>("");
  const [companySize, setCompanySize] = useState<string>("");
  const [useCase, setUseCase] = useState<UseCase | "">("");
  const [submitting, setSubmitting] = useState(false);

  const skip = () => {
    if (typeof window !== "undefined") {
      window.localStorage.setItem(DISMISS_KEY, "1");
    }
    setSkipped(true);
    setOpen(false);
  };

  const submit = async (selectedUseCase: UseCase) => {
    setSubmitting(true);
    try {
      const res = await fetch("/api/organizations/enrich", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          industry,
          company_size: companySize,
          primary_use_case: selectedUseCase,
        }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail ?? body.message ?? "Save failed.");
      }
      toast.success("Thanks! Company Brain is now tuned for your team.");
      await refresh();
      setOpen(false);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Couldn't save your answers.");
    } finally {
      setSubmitting(false);
    }
  };

  if (!needsEnrichment) return null;

  return (
    <Dialog open={open} onOpenChange={(next) => !submitting && setOpen(next)}>
      <DialogContent
        className="max-w-md"
        // Block the click-outside escape — we want a deliberate skip.
        onPointerDownOutside={(e) => e.preventDefault()}
        onEscapeKeyDown={(e) => e.preventDefault()}
      >
        <DialogHeader>
          <DialogTitle>Tell us about your organization</DialogTitle>
          <DialogDescription>
            Takes 30 seconds. We&apos;ll tune Company Brain for the way your team
            uses it.
          </DialogDescription>
        </DialogHeader>

        {step === "industry" && (
          <div className="space-y-2">
            <p className="text-xs font-medium text-muted-foreground">
              What industry are you in?
            </p>
            <div className="grid grid-cols-2 gap-2">
              {INDUSTRIES.map((label) => (
                <button
                  key={label}
                  type="button"
                  onClick={() => {
                    setIndustry(label);
                    setStep("size");
                  }}
                  className={cn(
                    "rounded-lg border border-border bg-background px-3 py-2 text-left text-sm transition-colors hover:border-primary/40 hover:bg-muted",
                    industry === label && "border-primary bg-primary/5",
                  )}
                >
                  {label}
                </button>
              ))}
            </div>
          </div>
        )}

        {step === "size" && (
          <div className="space-y-2">
            <p className="text-xs font-medium text-muted-foreground">
              How many employees?
            </p>
            <div className="flex flex-wrap gap-2">
              {SIZES.map((size) => (
                <button
                  key={size}
                  type="button"
                  onClick={() => {
                    setCompanySize(size);
                    setStep("use_case");
                  }}
                  className={cn(
                    "rounded-lg border border-border bg-background px-4 py-2 text-sm transition-colors hover:border-primary/40 hover:bg-muted",
                    companySize === size && "border-primary bg-primary/5",
                  )}
                >
                  {size}
                </button>
              ))}
            </div>
          </div>
        )}

        {step === "use_case" && (
          <div className="space-y-2">
            <p className="text-xs font-medium text-muted-foreground">
              Primary use case?
            </p>
            <div className="space-y-2">
              {USE_CASES.map((uc) => (
                <button
                  key={uc.value}
                  type="button"
                  disabled={submitting}
                  onClick={() => {
                    setUseCase(uc.value);
                    void submit(uc.value);
                  }}
                  className={cn(
                    "w-full rounded-lg border border-border bg-background px-3 py-2.5 text-left transition-colors hover:border-primary/40 hover:bg-muted disabled:opacity-60",
                    useCase === uc.value && "border-primary bg-primary/5",
                  )}
                >
                  <div className="flex items-center justify-between gap-3">
                    <div>
                      <p className="text-sm font-medium text-foreground">
                        {uc.label}
                      </p>
                      <p className="text-xs text-muted-foreground">{uc.desc}</p>
                    </div>
                    {submitting && useCase === uc.value && (
                      <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
                    )}
                  </div>
                </button>
              ))}
            </div>
          </div>
        )}

        <DialogFooter className="gap-2 sm:gap-2">
          {step !== "industry" && (
            <Button
              variant="ghost"
              size="sm"
              disabled={submitting}
              onClick={() => setStep(step === "size" ? "industry" : "size")}
            >
              Back
            </Button>
          )}
          <Button
            variant="ghost"
            size="sm"
            disabled={submitting}
            onClick={skip}
          >
            Skip for now
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
